"""Offline conversation QA dry-run — drives the REAL engine (LLM + branch
classifier + booking extractor) with scripted customer turns. No SMS, no DB,
no scheduler writes.

PERSONA-DRIVEN since 2026-08-18: the 6 service scenarios are TEMPLATES filled
from PERSONA_VOCAB. Adding a new vertical = copy a 5-line vocab block + a
prompt YAML — the same scenarios then run against that business type with the
right vocabulary (issue, emergency, price item, town). No scenario rewrites.

Usage:
    python qa_dry_run.py --persona plumbing   # one service vertical
    python qa_dry_run.py --persona sales      # live sales persona
    python qa_dry_run.py                      # all defined personas

Output: console summary + transcripts to data/qa/qa_run_<ts>_<persona>.md
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
OUT_DIR = PROJECT / "data" / "qa"

# ---------------------------------------------------------------------------
# Per-vertical vocabulary. Add a new business model here (5 lines) + a prompt
# YAML in prompts/ and the whole scenario suite runs against it for free.
# BUSINESS_NAME / SERVICE_AREA are set as env overrides BEFORE config import
# (get_settings() is @lru_cache'd — env must be set before first import).
# ---------------------------------------------------------------------------
PERSONA_VOCAB: dict[str, dict[str, str]] = {
    "plumbing": {
        "BUSINESS_NAME": "Helena Plumbing Co",
        "SERVICE_AREA": "Helena, MT",
        "issue": "yeah my kitchen sink is clogged",
        "emergency": "my basement is flooding RIGHT NOW water everywhere!!",
        "price_item": "water heater install",
        "town": "Helena",
    },
    "hvac": {
        "BUSINESS_NAME": "Peak Performance Heating & Cooling",
        "SERVICE_AREA": "Helena, MT",
        "issue": "my AC stopped blowing cold air",
        "emergency": "my furnace just died and it's freezing in here!!",
        "price_item": "new furnace install",
        "town": "Helena",
    },
    "septic": {
        "BUSINESS_NAME": "Helena Septic & Well",
        "SERVICE_AREA": "Helena, MT",
        "issue": "my septic tank is backing up in the yard",
        "emergency": "raw sewage is backing up into my basement RIGHT NOW!!",
        "price_item": "new septic tank install",
        "town": "Helena",
    },
    "sales": {},
}

# The 6 SERVICE scenario templates — shared by every service vertical; {field}s
# are filled from PERSONA_VOCAB at build time.
SERVICE_SCENARIO_TEMPLATES: list[dict] = [
    dict(id="emergency", expected="emergency",
         turns=["{emergency}",
                "yes can you come now? address is 45 River Rd, and my name is Dana"],
         check="must react with urgency, flag emergency, get address — NOT try to sell"),
    dict(id="booking_simple", expected="booked",
         turns=["{issue}",
                "name's Tom, address 123 Main St, {town}",
                "tomorrow morning works, like 9 or 10"],
         check="must collect name/address/day/time and land on booked"),
    dict(id="price_pushback", expected="none",
         turns=["how much for a {price_item}?",
                "wow that's way more than I wanted to spend"],
         check="must NOT invent a price (rule: upfront quotes only), handle objection"),
    dict(id="vague_time", expected="none",
         turns=["sometime this week, whenever you guys can get here",
                "i dunno, tuesday afternoon maybe?",
                "ok yeah tuesday works, 1pm"],
         check="must pin down a specific day/time, not accept 'sometime'"),
    dict(id="wrong_number", expected="unqualified",
         turns=["who is this? I think you have the wrong number"],
         check="graceful exit, no pushing"),
    dict(id="rude_spam", expected="unqualified",
         turns=["shut up bot", "why are you texting me"],
         check="stays professional, no loop, doesn't get defensive"),
]

# Sales persona — no templates, it's a different job (sells the AI service).
SALES_SCENARIOS: list[dict] = [
    dict(id="prospect_interested", persona="sales", expected="hot_lead",
         turns=["yeah this sounds cool, send me the info",
                "ok im Bob, I run Bob's Plumbing in Butte, my number is 406-555-1234"],
         check="must collect name/business/phone for Sevin"),
    dict(id="prospect_pricing", persona="sales", expected="none",
         turns=["how much does this cost?"],
         check="MUST quote $200 setup + 10% of the jobs it books — NEVER 15%"),
    dict(id="prospect_skeptic", persona="sales", expected="none",
         turns=["i dunno man sounds like another ai scam"],
         check="reassure matter-of-factly, not pushy"),
    dict(id="prospect_not_interested", persona="sales", expected="unqualified",
         turns=["not interested, stop texting me"],
         check="graceful exit + referral ask per objectives"),
]


def build_scenarios() -> list[dict]:
    """Expand templates per persona. Service verticals share the 6 templates;
    sales has its own list. A persona with empty vocab (sales) gets no env."""
    scenarios: list[dict] = []
    for persona, vocab in PERSONA_VOCAB.items():
        templates = SALES_SCENARIOS if persona == "sales" else SERVICE_SCENARIO_TEMPLATES
        for t in templates:
            scn = dict(t)
            scn["persona"] = persona
            scn["turns"] = [turn.format(**vocab) for turn in t["turns"]]
            scenarios.append(scn)
    return scenarios


RED_FLAG_PATTERNS: list[tuple[str, str]] = [
    ("stale_15_pricing", r"\b15\s?%|fifteen percent|15 percent"),
    ("invented_job_price", r"\$\s?\d{2,}"),
    ("multi_question", r"\?[^?]*\?"),
    ("robotic", r"as an ai|language model|i'?m sorry,? i can'?t|i am not able to"),
    ("too_long_sms", r".{240,}"),
    ("aggressive", r"shut up|stop texting|you stupid|fuck|damn it|whatever,? fine"),
]


def red_flags(reply: str, persona: str) -> list[str]:
    hits = []
    for name, pat in RED_FLAG_PATTERNS:
        if name == "stale_15_pricing" and persona != "sales":
            continue  # only the sales persona ever mentions 15%
        if name == "invented_job_price" and persona == "sales":
            continue  # sales persona SHOULD quote $200 — not a red flag
        if re.search(pat, reply, re.IGNORECASE):
            hits.append(name)
    return hits


async def run_scenario(scn: dict) -> dict:
    from conversation import ConversationEngine

    engine = ConversationEngine(business_type=scn["persona"])
    history: list[dict] = []
    transcript = []
    initial = engine.get_initial_message()
    transcript.append(("AI", initial, "initial", "", {}))
    history.append({"role": "assistant", "content": initial})

    flags: list[str] = []
    final_branch = "none"
    for turn in scn["turns"]:
        history.append({"role": "user", "content": turn})
        try:
            reply, branch, reason, details = await engine.generate_reply(history)
        except Exception as e:
            transcript.append(("USER", turn, "", "", {}))
            transcript.append(("AI", f"<ERROR: {type(e).__name__}: {e}>", "error", "", {}))
            flags.append(f"llm_error_{type(e).__name__}")
            final_branch = "error"
            break
        transcript.append(("USER", turn, "", "", {}))
        transcript.append(("AI", reply, branch, reason, details))
        history.append({"role": "assistant", "content": reply})
        final_branch = branch
        flags.extend(red_flags(reply, scn["persona"]))
        if branch == "booked" and turn == scn["turns"][-1]:
            pass  # keep going only if more turns exist

    expected_ok = final_branch == scn["expected"]
    if not expected_ok:
        flags.append(f"branch_mismatch:got={final_branch},expected={scn['expected']}")
    return {
        "id": scn["id"], "persona": scn["persona"], "expected": scn["expected"],
        "final_branch": final_branch, "flags": sorted(set(flags)),
        "check": scn["check"], "transcript": transcript,
    }


def render_transcript(res: dict) -> str:
    lines = [f"### {res['id']} — expected: {res['expected']} | got: {res['final_branch']}"]
    for role, text, branch, reason, details in res["transcript"]:
        prefix = "🤖 AI" if role == "AI" else "👤 "
        extra = f"  [{branch}]" if branch and branch not in ("initial",) else ""
        lines.append(f"{prefix}{extra}: {text}")
        if reason:
            lines.append(f"      ↳ reason: {reason}")
        if details and any(details.values()):
            lines.append(f"      ↳ booking: {details}")
    if res["flags"]:
        lines.append(f"      ⚠️ FLAGS: {', '.join(res['flags'])}")
    lines.append("")
    return "\n".join(lines)


async def run_persona(persona: str, scenarios: list[dict]) -> list[dict]:
    print(f"\n=== Persona: {persona} ===")
    results = []
    for scn in [s for s in scenarios if s["persona"] == persona]:
        res = await run_scenario(scn)
        results.append(res)
        status = "✅" if not res["flags"] else "⚠️"
        print(f"  {status} {res['id']:<22} branch={res['final_branch']:<10} "
              f"expected={res['expected']:<10} flags={res['flags'] or 'none'}")
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=sorted(PERSONA_VOCAB), default=None)
    args = parser.parse_args()

    personas = [args.persona] if args.persona else list(PERSONA_VOCAB)
    scenarios = build_scenarios()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Set persona env overrides BEFORE importing config (lru_cache)
    for persona in personas:
        for k, v in PERSONA_VOCAB.get(persona, {}).items():
            if k in ("BUSINESS_NAME", "SERVICE_AREA"):
                os.environ[k] = v

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    for persona in personas:
        results = await run_persona(persona, scenarios)
        all_results.extend(results)
        report = [f"# QA Dry-Run — {persona} — {ts}", ""]
        report += [render_transcript(r) for r in results]
        out = OUT_DIR / f"qa_run_{ts}_{persona}.md"
        out.write_text("\n".join(report), encoding="utf-8")
        print(f"  transcript → {out}")

    total_flags = sum(len(r["flags"]) for r in all_results)
    print(f"\n=== SUMMARY: {len(all_results)} scenarios, {total_flags} flags ===")
    return 1 if total_flags else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
