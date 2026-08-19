"""Render a client's onboarding answers into a ready prompt YAML + QA vocab.

Usage:
    python onboarding/render_client.py onboarding/acme.yaml

Reads the answers sheet (see onboarding/client-questionnaire.md for the
question list + field meanings), then:
  1. Writes prompts/<client-slug>.yaml — fully personalized: identity,
     objectives (their services), emergency triggers, pricing fork, tone,
     guardrails, initial message naming their services.
  2. Prints the PERSONA_VOCAB block to paste into qa_dry_run.py so the QA
     suite tests the client with THEIR vocabulary.

Every service vertical's prompt is generated from the same fields, so a new
vertical = pick a business_type, answer the 8 questions, render. No hand
editing YAML per client.

Fields read from the answers YAML:
    client, business_type, business_name, service_area, services (list),
    emergency_triggers (list), pricing_policy (no_upfront_quotes |
    flat_rate_menu), business_hours, booking_horizon_days, owner_phone,
    guardrails (list), tone (casual|professional), review_link
Unknown business_type falls back to a generic service identity line.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parent.parent
PROMPTS = PROJECT / "prompts"
ONBOARDING = PROJECT / "onboarding"

TRADE_NOUN = {
    "plumbing": "plumbing",
    "hvac": "HVAC",
    "septic": "septic and well",
    "sales": "business automation",
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def render_prompt(a: dict) -> str:
    btype = a.get("business_type", "plumbing")
    trade = TRADE_NOUN.get(btype, btype)
    services = a.get("services") or []
    services_txt = ", ".join(services)
    triggers = a.get("emergency_triggers") or []
    triggers_txt = ", ".join(triggers)
    pricing = a.get("pricing_policy", "no_upfront_quotes")
    tone = a.get("tone", "casual")
    guardrails = a.get("guardrails") or []
    hours = a.get("business_hours", "")
    horizon = a.get("booking_horizon_days", 7)

    if tone == "professional":
        tone_line = "Be professional, courteous, and concise. Mirror a well-run office."
        greet = "We missed your call"
    else:
        tone_line = "Be warm and casual. Talk like a real person, not a robot."
        greet = "sorry I missed your call"

    rules = [
        tone_line,
        "If asked whether you're a robot/AI/real person: be honest — you're the AI assistant for the business, here to get them booked. Never claim to be a real person; saying you are is an instant trust-killer when they find out.",
        "Keep responses short — 1-3 sentences max.",
        "Never ask more than one question at a time.",
        "Wrap up efficiently — once you have the issue, address, and a time, confirm the booking and end the conversation. No small talk, no extra questions after it's booked.",
    ]
    if triggers:
        rules.append(f"If they mention {triggers_txt} → flag as emergency and get address ASAP.")
    else:
        rules.append("If the situation sounds dangerous or urgent (safety risk, active damage) → flag as emergency and get address ASAP.")
    if hours:
        rules.append(f"Offer appointments within business hours: {hours}.")
    if horizon:
        rules.append(f"Book within {horizon} days — do not offer dates beyond that.")
    if pricing == "flat_rate_menu" and services:
        rules.append(f"Quote from the flat-rate menu for common jobs ({services_txt}); for anything unusual, say pricing needs to be seen in person.")
    else:
        rules.append("Don't make up specific pricing — say pricing depends on the job and we provide upfront quotes.")
    for g in guardrails:
        g = g.strip()
        if not re.match(r"never\b", g, re.I):
            g = f"Never {g.lstrip().lower()}"
        rules.append(g)

    if services:
        initial = f"Hey {{{{business_name}}}} here — {greet}! What can we help with? {services_txt}, or something else?"
    else:
        initial = f"Hey {{{{business_name}}}} here — {greet}! What's going on?"

    doc = {
        "identity": (
            f"You are a friendly AI assistant for {{{{business_name}}}}, a professional "
            f"{trade} company serving {{{{service_area}}}}."
        ),
        "objectives": (
            f"Your goal in this conversation:\n"
            f"1. Find out what issue the customer is experiencing"
            f"{f' ({services_txt})' if services else ''}\n"
            f"2. Determine if it's an emergency\n"
            f"3. Collect their address and a preferred time for a service visit\n"
            f"4. Be warm, helpful, and professional"
        ),
        "rules": rules,
        "initial_message": initial,
        "max_ai_responses": 10,
        "response_delay_seconds": 35,
        "review_link": a.get("review_link", ""),
    }
    # yaml.safe_dump with block style for the long text fields; rules stay a
    # real YAML list (hand-built strings with ': ' inside got parsed as nested
    # mappings — that was the bug; dumping the dict is immune).
    return (
        f"# {a['business_name']} — Missed Call AI Text Back (rendered by render_client.py)\n"
        + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    )


def vocab_block(a: dict) -> str:
    services = a.get("services") or []
    triggers = a.get("emergency_triggers") or []
    issue = services[0] if services else "service issue"
    price_item = services[1] if len(services) > 1 else (services[0] if services else "job")
    emergency = triggers[0] if triggers else "an urgent situation right now!!"
    return f'''    "{slugify(a['client'])}": {{
        "BUSINESS_NAME": "{a['business_name']}",
        "SERVICE_AREA": "{a.get('service_area', '')}",
        "issue": "{issue}",
        "emergency": "{emergency}",
        "price_item": "{price_item}",
        "town": "{a.get('service_area', '').split(',')[0]}",
    }},'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    if not src.is_absolute():
        src = ONBOARDING / src if not src.parts or src.parts[0] != "onboarding" else PROJECT / src
    a = yaml.safe_load(src.read_text(encoding="utf-8"))

    slug = slugify(a["client"])
    out = PROMPTS / f"{slug}.yaml"
    out.write_text(render_prompt(a), encoding="utf-8")
    print(f"✅ wrote {out}")
    print(f"\n📋 paste into qa_dry_run.py PERSONA_VOCAB:\n{vocab_block(a)}")
    print(f"\nThen: python qa_dry_run.py --persona {slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
