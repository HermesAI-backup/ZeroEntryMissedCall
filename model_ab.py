"""Model A/B — conversation-quality comparison across candidate models.

Drives the REAL engine (persona prompts, branch classifier, extractor,
calendar injection) with the SAME scripted scenarios per model, then scores
replies for quality/naturalness. Extraction reliability is settled (see
model_benchmark.py — model-independent); this measures what the customer
actually experiences: naturalness, continuity (no re-asking known info),
no re-greeting, clean booking closes.

CRITICAL: LLMClient reads the model at construction and get_settings() is
@lru_cache'd — you CANNOT swap models inside one process. Each model runs
in a FRESH subprocess with LLM_MODEL set before any config import.

Usage:
    python model_ab.py                    # all models, each in its own subprocess
    python model_ab.py --model openai/gpt-5.6-luna   # single model, in-process
    python model_ab.py --models a,b,c     # subset (comma-separated, subprocesses)

Output: data/qa/ab_<model-sanitized>_<ts>.md per model + console summary.
Exit 0 always (models vary; a failed model is a data point, not a crash).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
OUT_DIR = PROJECT / "data" / "qa"

MODELS = [
    "deepseek/deepseek-v4-flash",   # current production model
    "openai/gpt-4.1-mini",          # current fallback
    "openai/gpt-5.6-luna",
    "x-ai/grok-4.6",
]

PERSONA_ENV = {
    "plumbing": {"BUSINESS_NAME": "Helena Plumbing Co", "SERVICE_AREA": "Helena, MT"},
    "sales": {},
}

# ---- Quality checks beyond qa_dry_run's red flags ---------------------------
# (id, applies-to-persona, pattern, explanation)
QUALITY_PATTERNS: list[tuple[str, str, str, str]] = [
    ("re_asks_address", "*", r"what'?s your address|your address|address please|what address",
     "customer already gave an address (or AI asked before) — re-asking = amnesia"),
    ("re_asks_time", "*", r"what (day|time)|which (day|time)|what'?s a good (day|time)",
     "customer already gave day/time — re-asking = amnesia"),
    ("regreet", "*", r"sorry i missed your call|sorry i couldn'?t answer",
     "re-greeting a conversation that is already past the initial message"),
    ("asks_phone_plumbing", "plumbing", r"what'?s your (phone|number)|your phone number",
     "plumbing persona never needs the customer's phone number"),
    ("no_question", "*", r"^[^?]*\.?$",
     "reply contains no question at all — dead-end or lecture, usually a fail"),
    ("stale_15", "sales", r"\b15\s?%|fifteen percent", "stale 15% pricing — must quote 10%"),
    ("invented_price", "plumbing", r"\$\s?\d{2,}", "invented a job price (rule: no upfront quotes)"),
    ("apology_loop", "*", r"i'?m sorry|apolog", "over-apologizing reads robotic in SMS"),
    ("excited_overkill", "*", r"!!+", "excessive exclamation = fake-sounding"),
    ("ai_language", "*", r"as an ai|language model|i'?m unable|i cannot assist",
     "bot-tell language"),
]

# Continuity knowledge map: if these were already stated by the customer in the
# history, the AI must NOT ask for them again.
KNOWN_INFO = {
    "address": r"\d+\s+\w+.*(?:st|street|ave|avenue|rd|road|dr|drive|lane|ln|blvd|ct|circle|way)",
    "date": r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|next week|this week|\d{1,2}/\d{1,2})",
    "time": r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)|at \d|(?:morning|afternoon|evening|noon)\b",
    "name": r"(?:my name['’]?s|i['’]?m|this is)\s+([A-Z][a-z]+)",
    "issue": r"(clog|leak|flood|install|replace|broken|drain|frozen|no hot water|repair|fix)",
}


def _known_present(history: list[dict], kind: str) -> bool:
    pat = re.compile(KNOWN_INFO[kind], re.IGNORECASE)
    for m in history:
        if m["role"] == "user" and pat.search(m["content"]):
            return True
    return False


def quality_flags(reply: str, persona: str, history: list[dict]) -> list[str]:
    flags = []
    for name, applies, pat, _why in QUALITY_PATTERNS:
        if applies != "*" and applies != persona:
            continue
        if name == "no_question" and len(reply) < 20:
            continue  # short confirmations are fine without a question
        if re.search(pat, reply, re.IGNORECASE):
            flags.append(name)
    # continuity: never re-ask for info the customer already provided
    if _known_present(history, "address") and re.search(r"what'?s your address|your address", reply, re.IGNORECASE):
        flags.append("re_asks_address")
    if _known_present(history, "time") and re.search(r"what (day|time)|which (day|time)|what'?s a good (day|time)", reply, re.IGNORECASE):
        flags.append("re_asks_time")
    if _known_present(history, "date") and re.search(r"what (day|time)|which (day|time)", reply, re.IGNORECASE):
        flags.append("re_asks_time")
    return sorted(set(flags))


# ---- Scenario set (mirrors qa_dry_run + continuity/quality scenarios) -------
SCENARIOS: list[dict] = [
    dict(id="booking_simple", persona="plumbing", expected="booked",
         turns=["yeah my kitchen sink is clogged",
                "name's Tom, address 123 Main St, Helena",
                "tomorrow morning works, like 9 or 10"],
         check="collect name/address/day/time, land on booked, confirm cleanly"),
    dict(id="continuity_mid_booking", persona="plumbing", expected="booked",
         turns=["my bathroom sink is dripping",
                "3241 harry st helena",
                "early afternoon im available the next 3 days",
                "ok wednesday at 1pm then"],
         check="NEVER re-ask address/time already given; book Wed 13:00"),
    dict(id="vague_time", persona="plumbing", expected="none",
         turns=["sometime this week, whenever you guys can get here",
                "i dunno, tuesday afternoon maybe?",
                "ok yeah tuesday works, 1pm"],
         check="pin down a specific day/time, don't accept 'sometime'"),
    dict(id="price_pushback", persona="plumbing", expected="none",
         turns=["how much for a water heater install?",
                "wow that's way more than I wanted to spend"],
         check="no invented price, handle objection, don't vanish"),
    dict(id="emergency_flood", persona="plumbing", expected="emergency",
         turns=["my basement is flooding RIGHT NOW water everywhere!!",
                "yes can you come now? address is 45 River Rd, my name is Dana"],
         check="react with urgency, get address, NOT sell"),
    dict(id="wrong_number", persona="plumbing", expected="unqualified",
         turns=["who is this? I think you have the wrong number"],
         check="graceful exit, no pushing"),
    dict(id="rude_spam", persona="plumbing", expected="unqualified",
         turns=["shut up bot", "why are you texting me"],
         check="stays professional, no loop, doesn't get defensive"),
    dict(id="prospect_interested", persona="sales", expected="hot_lead",
         turns=["yeah this sounds cool, send me the info",
                "ok im Bob, I run Bob's Plumbing in Butte, my number is 406-555-1234"],
         check="collect name/business/phone for Sevin"),
    dict(id="prospect_pricing", persona="sales", expected="none",
         turns=["how much does this cost?"],
         check="quote $200 setup + 10% of jobs it books — NEVER 15%"),
    dict(id="prospect_skeptic", persona="sales", expected="none",
         turns=["i dunno man sounds like another ai scam"],
         check="reassure matter-of-factly, not pushy"),
]


async def run_scenario(scn: dict, model: str) -> dict:
    from conversation import ConversationEngine

    engine = ConversationEngine(business_type=scn["persona"])
    history: list[dict] = []
    transcript = []
    initial = engine.get_initial_message()
    transcript.append({"role": "AI", "text": initial, "branch": "initial", "flags": []})
    history.append({"role": "assistant", "content": initial})

    flags: list[str] = []
    final_branch = "none"
    for turn in scn["turns"]:
        history.append({"role": "user", "content": turn})
        try:
            reply, branch, reason, details = await engine.generate_reply(history)
        except Exception as e:
            transcript.append({"role": "USER", "text": turn, "branch": "", "flags": []})
            transcript.append({"role": "AI", "text": f"<ERROR {type(e).__name__}: {e}>",
                               "branch": "error", "flags": ["llm_error"]})
            flags.append("llm_error")
            final_branch = "error"
            break
        transcript.append({"role": "USER", "text": turn, "branch": "", "flags": []})
        tflags = quality_flags(reply, scn["persona"], history)
        transcript.append({"role": "AI", "text": reply, "branch": branch, "flags": tflags})
        flags.extend(tflags)
        history.append({"role": "assistant", "content": reply})
        final_branch = branch
        if branch in ("booked", "unqualified", "emergency") and turn == scn["turns"][-1]:
            pass

    if final_branch != scn["expected"]:
        flags.append(f"branch_mismatch:got={final_branch},expected={scn['expected']}")
    # booking completeness on booked branches
    if final_branch == "booked":
        meta = details or {}
        for k in ("customer_name", "address", "appt_date", "appt_time"):
            if not meta.get(k):
                flags.append(f"booking_missing:{k}")

    return {"id": scn["id"], "persona": scn["persona"], "expected": scn["expected"],
            "final_branch": final_branch, "flags": flags, "transcript": transcript}


def render(results: list[dict], model: str, ts: str) -> str:
    lines = [f"# Model A/B — {model} — {ts}", "",
             "| scenario | expected | got | flags |", "|---|---|---|---|"]
    total_flags = 0
    for r in results:
        total_flags += len(r["flags"])
        lines.append(f"| {r['id']} | {r['expected']} | {r['final_branch']} | {', '.join(r['flags']) or '—'} |")
    lines.append(f"\n**TOTAL FLAGS: {total_flags}**\n")
    for r in results:
        lines.append(f"### {r['id']} (expected {r['expected']}, got {r['final_branch']})")
        for m in r["transcript"]:
            who = "🤖 AI" if m["role"] == "AI" else "👤  "
            extra = f"  [{m['branch']}]" if m["branch"] else ""
            lines.append(f"{who}{extra}: {m['text']}")
            if m.get("flags"):
                lines.append(f"      ⚠️ {', '.join(m['flags'])}")
        lines.append("")
    return "\n".join(lines)


async def run_model(model: str) -> int:
    """Run all scenarios for ONE model (in-process; env already set)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n=== MODEL: {model} ===", flush=True)
    results = []
    for scn in SCENARIOS:
        for k, v in PERSONA_ENV.get(scn["persona"], {}).items():
            os.environ[k] = v
        res = await run_scenario(scn, model)
        results.append(res)
        print(f"  {'✅' if not res['flags'] else '⚠️'} {res['id']:<24} "
              f"branch={res['final_branch']:<10} flags={res['flags'] or 'none'}", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    out = OUT_DIR / f"ab_{safe}_{ts}.md"
    out.write_text(render(results, model, ts), encoding="utf-8")
    print(f"  transcript → {out}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="single model (in-process)")
    parser.add_argument("--models", default=None, help="comma-separated subset (subprocesses)")
    args = parser.parse_args()

    if args.model:
        os.environ["LLM_MODEL"] = args.model
        return asyncio.run(run_model(args.model))

    models = MODELS
    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]

    code = 0
    for m in models:
        env = os.environ.copy()
        env["LLM_MODEL"] = m
        # fresh subprocess per model — lru_cache'd settings must not leak
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            "--model", m], env=env, cwd=str(PROJECT))
        if p.returncode != 0:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
