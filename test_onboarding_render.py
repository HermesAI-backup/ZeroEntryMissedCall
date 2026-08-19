"""Regression test: onboarding/render_client.py prompt generation (2026-08-18).

Guards the client-onboarding renderer:
  - required prompt fields present after render
  - rules is a list of STRINGS (the ': ' nested-mapping YAML bug — "business
    hours: Mon-Fri" parsed as a dict and crashed the QA loop)
  - pricing fork: no_upfront_quotes vs flat_rate_menu produce different rules
  - tone fork: casual vs professional
  - guardrails are appended verbatim-ish (never- prefix)
  - {{business_name}}/{{service_area}} template vars survive into the YAML
  - vocab_block emits a persona key usable by qa_dry_run build_scenarios
"""
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onboarding import render_client  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    if not cond:
        FAILS.append(name)


def render_and_load(name: str) -> dict:
    src = Path(__file__).parent / "onboarding" / name
    a = yaml.safe_load(src.read_text(encoding="utf-8"))
    text = render_client.render_prompt(a)
    return yaml.safe_load(text)


acme = render_and_load("acme.yaml")
bigsky = render_and_load("bigsky.yaml")

REQ = ["identity", "objectives", "rules", "initial_message",
       "max_ai_responses", "response_delay_seconds", "review_link"]
check("acme required fields", all(k in acme for k in REQ))
check("bigsky required fields", all(k in bigsky for k in REQ))

check("acme rules all strings",
      isinstance(acme["rules"], list) and all(isinstance(r, str) for r in acme["rules"]))
check("bigsky rules all strings",
      isinstance(bigsky["rules"], list) and all(isinstance(r, str) for r in bigsky["rules"]))

# pricing fork
check("acme has no-upfront-quotes rule",
      any("no upfront quotes" in r or "Don't make up specific pricing" in r for r in acme["rules"]))
check("bigsky has flat-rate-menu rule",
      any("flat-rate menu" in r for r in bigsky["rules"]))
check("bigsky does NOT have no-upfront rule",
      not any("Don't make up specific pricing" in r for r in bigsky["rules"]))

# tone fork
check("acme casual tone", any("warm and casual" in r for r in acme["rules"]))
check("bigsky professional tone", any("professional, courteous" in r for r in bigsky["rules"]))

# guardrails appended
check("bigsky guardrail 1",
      any("never promise 24/7" in r for r in bigsky["rules"]))
check("bigsky guardrail 2",
      any("never mention competitors" in r for r in bigsky["rules"]))

# honesty rule (2026-08-18 adversarial finding: service AI lied "I'm a real
# person" when asked; the renderer must always carry the honesty rule)
check("acme honesty rule present",
      any("Never claim to be a real person" in r for r in acme["rules"]))
check("bigsky honesty rule present",
      any("Never claim to be a real person" in r for r in bigsky["rules"]))

# template vars survive
check("acme identity keeps {{business_name}}", "{{business_name}}" in acme["identity"])
check("acme identity keeps {{service_area}}", "{{service_area}}" in acme["identity"])
check("acme initial_message keeps {{business_name}}", "{{business_name}}" in acme["initial_message"])

# vocab_block emits a key build_scenarios accepts (structural smoke)
acme_a = yaml.safe_load((Path(__file__).parent / "onboarding" / "acme.yaml").read_text(encoding="utf-8"))
vb = render_client.vocab_block(acme_a)
check("vocab_block has BUSINESS_NAME", "Acme Plumbing" in vb)
check("vocab_block has issue", '"issue": "clogs"' in vb)

# emergency triggers reach rules
check("acme emergency triggers", any("no water, gas smell, sewage backup" in r for r in acme["rules"]))

# hours + horizon reach rules
check("acme hours rule", any("Mon-Fri 8-6" in r for r in acme["rules"]))
check("acme horizon rule", any("Book within 7 days" in r for r in acme["rules"]))
check("bigsky horizon rule", any("Book within 14 days" in r for r in bigsky["rules"]))

if FAILS:
    print(f"FAIL ({len(FAILS)}): {', '.join(FAILS)}")
    sys.exit(1)
print("PASS: onboarding renderer (all checks)")
