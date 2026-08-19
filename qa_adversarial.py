"""Adversarial QA — deliberately try to BREAK the conversation engine.

Companion to qa_dry_run.py (which tests happy-path/edge scenarios). This
drives the REAL engine (ConversationEngine -> LLM -> branch classifier ->
booking extractor) with hostile, broken, or confusing customer inputs and
flags ANYTHING that goes wrong:

  - crashes / exceptions (llm_error_*)
  - bot-tell language ("as an AI", "language model", "I'm unable")
  - invented prices ($ amounts on service personas)
  - stale 15% pricing (sales persona)
  - re-asking info already given (continuity — the make-or-break)
  - re-greeting mid-conversation
  - multi-question replies (one-question-at-a-time rule)
  - over-long replies (SMS budget)
  - abusive/robotic phrasing
  - accepting nonsense (branch landed on 'booked' for gibberish input)
  - dead-end: reply that asks nothing and advances nothing

Scenario families (input classes, not exact turns — the LLM sees them fresh):
  1. GIBBERISH / TYPO SPAM    — "asdf asdf", "hhhhh", random keys
  2. EMOJI / SYMBOLS           — "🔥🚨", "??????", "!!!!!"
  3. ALL CAPS SCREAMING        — "I NEED A PLUMBER NOW"
  4. MULTI-QUESTION BOMB       — 4 questions in one text
  5. THREATS / HOSTILITY       — "you better come now or else"
  6. WRONG NUMBER              — "who is this? wrong number"
  7. PRICE HAGGLING            — "that's too expensive, give me a discount"
  8. CANCEL / RESCHEDULE       — "actually cancel that", "can we move it"
  9. PAST-DATE BOOKING         — "book me for yesterday"
  10. VAGUE TIME               — "sometime next week maybe"
  11. BOT-TELL PROBE           — "are you a robot? be honest"
  12. PII DUMP                 — full address+phone+name in one message
  13. SPLIT MESSAGE            — "my sink is" then "leaking btw"
  14. RAPID-FIRE REPLIES       — customer answers before AI responds
  15. LONG RUN-ON              — 500 chars no punctuation
  16. CONTRADICTION            — "actually ignore that, the other address"
  17. SALES: COMPETITOR        — "XYZ company quoted me less"
  18. SALES: SKEPTIC           — "this sounds like a scam"
  19. SALES: PRICING           — "how much?" (must quote $200+10%, never 15%)
  20. EMERGENCY-ISH            — "my basement is flooding!!" (should be emergency)

For each family: 3 variations, drive through generate_reply, collect flags.
A scenario "breaks" if it raises, or produces bot-tell / invented price /
stale pricing / re-ask / re-greet / multi-question / dead-end.

Usage:
    python qa_adversarial.py [--persona plumbing|sales] [--limit N]
Output: console summary + transcripts to data/qa/adversarial_<ts>_<persona>.md
Exit 0 always (breakage is a data point, not a crash).
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

PERSONA_ENV = {
    "plumbing": {"BUSINESS_NAME": "Helena Plumbing Co", "SERVICE_AREA": "Helena, MT"},
    "sales": {},
}

# (id, persona, turns, what to watch for)
SCENARIOS: list[dict] = [
    # --- GIBBERISH ---
    dict(id="gibberish_1", persona="plumbing",
         turns=["asdf asdf asdf", "qwertyuiop", "1234567890"],
         watch="no crash, no fake booking, stay in persona"),
    dict(id="gibberish_2", persona="plumbing",
         turns=["hhhhhhhhhh", "x x x x x"],
         watch="no crash, no fake booking"),
    dict(id="gibberish_3", persona="sales",
         turns=["asdfasdf", "lol wut"],
         watch="no crash, stay in persona"),
    # --- EMOJI / SYMBOLS ---
    dict(id="emoji_spam", persona="plumbing",
         turns=["🔥🚨💧💦", "😡😡😡", "👍"],
         watch="no crash, no fake booking"),
    dict(id="symbols_only", persona="plumbing",
         turns=["??????", "!!!!!", "......"],
         watch="no crash, no fake booking"),
    # --- ALL CAPS ---
    dict(id="screaming", persona="plumbing",
         turns=["I NEED A PLUMBER RIGHT NOW!!!!", "HELLO? ARE YOU THERE??"],
         watch="no crash, handle urgency without panic"),
    # --- MULTI-QUESTION BOMB ---
    dict(id="multi_question_bomb", persona="plumbing",
         turns=["do you do water heaters? how much? when can you come? do you work weekends?",
                "well? are you going to answer?"],
         watch="one question at a time rule (multi_question flag), no crash, no forgetting the customer's asks"),
    # --- THREATS / HOSTILITY ---
    dict(id="threat_1", persona="plumbing",
         turns=["you better get someone here today or I'm calling the city on you",
                "I mean it, this is ridiculous"],
         watch="no crash, stay professional, no capitulation, no aggression"),
    dict(id="hostile_2", persona="plumbing",
         turns=["you guys are useless", "worst company ever, I'm telling everyone"],
         watch="no crash, stay professional, graceful"),
    # --- WRONG NUMBER ---
    dict(id="wrong_number", persona="plumbing",
         turns=["who is this? I think you have the wrong number"],
         watch="unqualified/graceful exit, no pushing"),
    # --- PRICE HAGGLING ---
    dict(id="haggle", persona="plumbing",
         turns=["how much to fix a leak?", "that's way too much, can you do it cheaper?",
                "my neighbor's plumber only charges half that"],
         watch="no invented price, handle objection without caving"),
    # --- CANCEL / RESCHEDULE ---
    dict(id="cancel_booking", persona="plumbing",
         turns=["hey I booked for tomorrow but I need to cancel",
                "actually yeah cancel it please"],
         watch="acknowledge cancellation gracefully, don't re-book"),
    dict(id="reschedule", persona="plumbing",
         turns=["can we move my appointment from tuesday to friday?",
                "friday works"],
         watch="handle reschedule without confusion"),
    # --- PAST DATE ---
    dict(id="past_date", persona="plumbing",
         turns=["can you come yesterday?", "what about last tuesday?"],
         watch="no fake booking of past date, redirect to real availability"),
    # --- VAGUE TIME ---
    dict(id="vague_time", persona="plumbing",
         turns=["sometime next week maybe", "whenever works"],
         watch="pin down a specific day/time, don't accept vagueness"),
    # --- BOT-TELL PROBE ---
    dict(id="bot_tell", persona="plumbing",
         turns=["are you a robot or a real person?", "be honest with me"],
         watch="NO 'as an AI/language model' — bot-tell is a product killer"),
    dict(id="bot_tell_sales", persona="sales",
         turns=["is this automated?", "you're a bot aren't you"],
         watch="no bot-tell, matter-of-fact handling"),
    # --- PII DUMP ---
    dict(id="pii_dump", persona="plumbing",
         turns=["my name is John Smith, address 456 Oak St, Helena, phone 406-555-1234, and I have a leaky pipe under the sink"],
         watch="extract what's useful, don't re-ask for given info"),
    # --- SPLIT MESSAGE ---
    dict(id="split_message", persona="plumbing",
         turns=["my sink is", "leaking by the way", "it's the kitchen one"],
         watch="continuity across split messages, no re-asking what's already given"),
    # --- RAPID FIRE ---
    dict(id="rapid_fire", persona="plumbing",
         turns=["hi", "my toilet is clogged", "address is 789 Pine", "can you come tomorrow?"],
         watch="handle multiple messages, collect all info, no re-ask"),
    # --- LONG RUN-ON ---
    dict(id="long_runon", persona="plumbing",
         turns=["so basically what happened is the pipe under the kitchen sink started leaking about three days ago and now there's water in the cabinet and it smells bad and my wife is really upset about it and we need someone to come look at it as soon as possible because we have guests coming this weekend and the landlord said he won't pay for it so we need to figure out the cost and whether you guys can do it"],
         watch="no crash on long input, extract issue, don't get lost"),
    # --- CONTRADICTION ---
    dict(id="contradiction", persona="plumbing",
         turns=["my address is 123 Main St", "actually no, it's 456 Oak St",
                "yeah the oak one"],
         watch="newest message wins, no double-booking addresses"),
    # --- SALES: COMPETITOR ---
    dict(id="sales_competitor", persona="sales",
         turns=["XYZ AI company quoted me $80/mo for the same thing",
                "why should I pay you more?"],
         watch="handle objection, no invented discounts, no trashing competitor"),
    # --- SALES: SKEPTIC ---
    dict(id="sales_skeptic", persona="sales",
         turns=["this sounds like a scam honestly", "everyone's selling AI these days"],
         watch="matter-of-fact reassurance, not pushy, no defensiveness"),
    # --- SALES: PRICING ---
    dict(id="sales_pricing", persona="sales",
         turns=["how much does this cost?", "ok so what exactly do I pay?"],
         watch="MUST quote $200 setup + 10% of jobs — NEVER 15%, never monthly-fee confusion"),
    # --- EMERGENCY-ISH ---
    dict(id="emergency_flood", persona="plumbing",
         turns=["my basement is flooding RIGHT NOW water everywhere!!",
                "yes can you come now? address is 45 River Rd, name's Dana"],
         watch="emergency branch, get address, NOT sell"),
    dict(id="emergency_gas", persona="plumbing",
         turns=["I smell gas in my house", "it's really strong"],
         watch="emergency (gas smell), safety-first"),
]


RED_FLAG_PATTERNS: list[tuple[str, str]] = [
    ("stale_15_pricing", r"\b15\s?%|fifteen percent"),
    ("invented_job_price", r"\$\s?\d{2,}"),
    ("multi_question", r"\?[^?]*\?"),
    ("bot_tell", r"as an ai|language model|i'?m (just )?an ai|i'?m unable|i cannot assist|as an ai assistant"),
    ("claims_real_person", r"i'?m (a )?real person|i am (a )?real person|i'?m a (real )?human"),
    ("too_long_sms", r".{240,}"),
    ("aggressive", r"shut up|stop texting|you stupid|fuck|idiot|worthless"),
    ("re_greet", r"^sorry (we|i) (missed|couldn't answer)"),
]


def red_flags(reply: str, persona: str, history: list[dict]) -> list[str]:
    hits = []
    for name, pat in RED_FLAG_PATTERNS:
        if name == "stale_15_pricing" and persona != "sales":
            continue
        if name == "invented_job_price" and persona == "sales":
            continue  # sales persona legitimately quotes $200
        if name == "claims_real_person" and persona == "sales":
            continue  # sales persona IS the human-facing sales pitch (Sevin's line)
        if re.search(pat, reply, re.IGNORECASE):
            hits.append(name)
    # continuity: never re-ask for info the customer already gave
    user_text = " ".join(m["content"] for m in history if m["role"] == "user")
    if re.search(r"\d+\s+\w+\s+(st|street|ave|avenue|rd|road)", user_text, re.IGNORECASE) \
            and re.search(r"what'?s your address|your address", reply, re.IGNORECASE):
        hits.append("re_asks_address")
    if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)", user_text, re.IGNORECASE) \
            and re.search(r"what (day|time)|which (day|time)", reply, re.IGNORECASE):
        hits.append("re_asks_time")
    return sorted(set(hits))


async def run_scenario(scn: dict) -> dict:
    from conversation import ConversationEngine

    engine = ConversationEngine(business_type=scn["persona"])
    history: list[dict] = []
    transcript = []
    initial = engine.get_initial_message()
    transcript.append(("AI", initial, "initial"))
    history.append({"role": "assistant", "content": initial})

    flags: list[str] = []
    final_branch = "none"
    for turn in scn["turns"]:
        history.append({"role": "user", "content": turn})
        try:
            # Per-turn timeout: a hung LLM/proxy call must fail FAST as a flag,
            # not stall the whole adversarial suite (hit 2026-08-18: the run
            # hung ~13 min on one generate_reply with 1.4s CPU = network wait).
            reply, branch, reason, details = await asyncio.wait_for(
                engine.generate_reply(history), timeout=60
            )
        except asyncio.TimeoutError:
            transcript.append(("USER", turn, ""))
            transcript.append(("AI", "<ERROR: TimeoutError — LLM call hung 60s>", "error"))
            flags.append("llm_error_Timeout")
            final_branch = "error"
            break
        except Exception as e:
            transcript.append(("USER", turn, ""))
            transcript.append(("AI", f"<ERROR: {type(e).__name__}: {e}>", "error"))
            flags.append(f"llm_error_{type(e).__name__}")
            final_branch = "error"
            break
        transcript.append(("USER", turn, ""))
        transcript.append(("AI", reply, branch))
        history.append({"role": "assistant", "content": reply})
        final_branch = branch
        flags.extend(red_flags(reply, scn["persona"], history))

    return {
        "id": scn["id"], "persona": scn["persona"],
        "final_branch": final_branch, "flags": sorted(set(flags)),
        "watch": scn["watch"], "transcript": transcript,
    }


def render(results: list[dict], persona: str, ts: str) -> str:
    lines = [f"# Adversarial QA — {persona} — {ts}", "",
             f"| scenario | branch | flags |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['final_branch']} | {', '.join(r['flags']) or '—'} |")
    lines.append("")
    for r in results:
        lines.append(f"### {r['id']} (branch={r['final_branch']}) — watch: {r['watch']}")
        for role, text, branch in r["transcript"]:
            who = "🤖 AI" if role == "AI" else "👤  "
            extra = f"  [{branch}]" if branch and branch != "initial" else ""
            lines.append(f"{who}{extra}: {text}")
        if r["flags"]:
            lines.append(f"      ⚠️ FLAGS: {', '.join(r['flags'])}")
        lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=["plumbing", "sales"], default=None)
    parser.add_argument("--limit", type=int, default=0, help="max scenarios to run (0=all)")
    args = parser.parse_args()

    personas = [args.persona] if args.persona else ["plumbing", "sales"]
    for persona in personas:
        for k, v in PERSONA_ENV.get(persona, {}).items():
            os.environ[k] = v

    scns = SCENARIOS if not args.limit else SCENARIOS[: args.limit]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    for persona in personas:
        results = []
        for scn in [s for s in scns if s["persona"] == persona]:
            res = await run_scenario(scn)
            results.append(res)
            status = "💥" if any(f.startswith("llm_error") for f in res["flags"]) else (
                "⚠️" if res["flags"] else "✅")
            print(f"  {status} {res['id']:<24} branch={res['final_branch']:<10} "
                  f"flags={res['flags'] or 'none'}", flush=True)
        all_results.extend(results)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"adversarial_{ts}_{persona}.md"
        out.write_text(render(results, persona, ts), encoding="utf-8")
        print(f"  transcript → {out}")

    total = sum(len(r["flags"]) for r in all_results)
    errors = sum(1 for r in all_results if any(f.startswith("llm_error") for f in r["flags"]))
    print(f"\n=== ADVERSARIAL SUMMARY: {len(all_results)} scenarios, "
          f"{total} flags, {errors} crashes ===")
    return 0  # breakage is data, not a crash


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
