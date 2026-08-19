"""Regression test: qa_dry_run.py persona-driven scenario expansion (2026-08-18).

Guards the refactor that turned hardcoded per-persona scenarios into shared
SERVICE_SCENARIO_TEMPLATES filled from PERSONA_VOCAB. Pins:
  - every service persona (plumbing/hvac/septic) expands to the same 6 ids
  - sales keeps its own 4-scenario list
  - vocab fills reach the customer turns (issue/emergency/price_item/town)
  - plumbing turns are BYTE-IDENTICAL to the pre-refactor hardcoded strings
    (the no-regression guarantee — same suite, different vocabulary source)
  - red-flag scope: invented_job_price fires for ALL service personas, never
    sales; stale_15_pricing fires for sales only
  - every service persona in PERSONA_VOCAB has a matching prompts/<p>.yaml

Plain asserts, exit 0/1 — matches repo convention (no pytest).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_dry_run  # noqa: E402  (module-level import after path fix)

FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    if not cond:
        FAILS.append(name)


# ---- persona sets ---------------------------------------------------------
SERVICE_IDS = ["emergency", "booking_simple", "price_pushback",
               "vague_time", "wrong_number", "rude_spam"]
SALES_IDS = ["prospect_interested", "prospect_pricing",
             "prospect_skeptic", "prospect_not_interested"]

scs = qa_dry_run.build_scenarios()
by_persona: dict[str, list[dict]] = {}
for s in scs:
    by_persona.setdefault(s["persona"], []).append(s)

check("22 total scenarios (6x3 service + 4 sales)", len(scs) == 22)
for p in ("plumbing", "hvac", "septic"):
    ids = [s["id"] for s in by_persona.get(p, [])]
    check(f"{p}: same 6 service ids", ids == SERVICE_IDS)
sales_ids = [s["id"] for s in by_persona.get("sales", [])]
check("sales: own 4 scenarios", sales_ids == SALES_IDS)

# ---- vocab reaches the customer turns -------------------------------------
hvac = {s["id"]: s for s in by_persona["hvac"]}
check("hvac booking uses issue vocab",
      hvac["booking_simple"]["turns"][0] == "my AC stopped blowing cold air")
check("hvac emergency uses emergency vocab",
      "furnace" in hvac["emergency"]["turns"][0])
check("hvac price uses price_item vocab",
      hvac["price_pushback"]["turns"][0] == "how much for a new furnace install?")
septic = {s["id"]: s for s in by_persona["septic"]}
check("septic booking uses issue vocab",
      "septic" in septic["booking_simple"]["turns"][0])
check("hvac address turn carries town vocab",
      hvac["booking_simple"]["turns"][1].endswith("Helena"))

# ---- plumbing byte-identical to pre-refactor strings -----------------------
plumb = {s["id"]: s for s in by_persona["plumbing"]}
check("plumbing emergency identical",
      plumb["emergency"]["turns"] ==
      ["my basement is flooding RIGHT NOW water everywhere!!",
       "yes can you come now? address is 45 River Rd, and my name is Dana"])
check("plumbing booking_simple identical",
      plumb["booking_simple"]["turns"] ==
      ["yeah my kitchen sink is clogged",
       "name's Tom, address 123 Main St, Helena",
       "tomorrow morning works, like 9 or 10"])
check("plumbing price_pushback identical",
      plumb["price_pushback"]["turns"] ==
      ["how much for a water heater install?",
       "wow that's way more than I wanted to spend"])
check("plumbing vague_time identical",
      plumb["vague_time"]["turns"] ==
      ["sometime this week, whenever you guys can get here",
       "i dunno, tuesday afternoon maybe?",
       "ok yeah tuesday works, 1pm"])
check("plumbing wrong_number identical",
      plumb["wrong_number"]["turns"] ==
      ["who is this? I think you have the wrong number"])
check("plumbing rude_spam identical",
      plumb["rude_spam"]["turns"] == ["shut up bot", "why are you texting me"])

# ---- red-flag scoping ------------------------------------------------------
check("invented price flags service personas",
      "invented_job_price" in qa_dry_run.red_flags("that'll be $350", "hvac"))
check("invented price flags plumbing",
      "invented_job_price" in qa_dry_run.red_flags("that'll be $350", "plumbing"))
check("invented price NEVER flags sales",
      "invented_job_price" not in qa_dry_run.red_flags("$200 setup", "sales"))
check("stale 15 flags sales only",
      "stale_15_pricing" in qa_dry_run.red_flags("15% of jobs", "sales")
      and "stale_15_pricing" not in qa_dry_run.red_flags("15% of jobs", "hvac"))

# ---- every service persona has a prompt YAML -------------------------------
for p in ("plumbing", "hvac", "septic"):
    check(f"prompts/{p}.yaml exists", (Path(__file__).parent / "prompts" / f"{p}.yaml").exists())

if FAILS:
    print(f"FAIL ({len(FAILS)}): {', '.join(FAILS)}")
    sys.exit(1)
print("PASS: qa_dry_run persona-driven expansion (all checks)")
