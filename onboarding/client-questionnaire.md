# Client Onboarding Questionnaire — Missed Call AI

One short list of questions per client. Answers feed the prompt YAML, the
QA vocabulary, and the client record — so personalization happens once, at
signup, and testing runs on the client's REAL vocabulary (issue/emergency/
price_item) instead of generic plumbing lines.

**Rule: only ask what we can't find ourselves.** Never ask the client for
anything on the "we already know" list.

## We already know (do NOT ask — from the lead/outreach)

- Business name, industry/vertical, city
- Phone number, website, service area town
- Google review link (from their Google Business profile, if public)

## The questions (ask the owner, 5-minute call or form)

### 1. What exactly do you do? (services)
*"What are the main jobs you get calls for?"*
→ Free-text list, e.g. *"clogs, water heaters, repipes, camera inspection"*.
**Feeds:** prompt `objectives` + `rules` (what's an emergency for THIS trade),
`PERSONA_VOCAB.issue` / `.price_item`, branch triggers.

### 2. What counts as an emergency for you?
*"If a customer texts at 2am, which situations do you actually want to be
woken up for?"*
→ e.g. *"no water, gas smell, sewage backup — not a dripping faucet"*.
**Feeds:** prompt `rules` emergency line + emergency branch threshold +
owner-alert policy (who gets woken, when).

### 3. Do you quote prices over text, or only after seeing the job?
*"When a customer asks 'how much?', what should the AI say?"*
→ e.g. *"flat-rate menu for common jobs"* or *"always 'needs to be seen,
no upfront quotes'"*.
**Feeds:** prompt `rules` pricing line — **this is the single biggest
per-client behavior fork**; the plumbing default is "no upfront quotes".

### 4. What are your business hours for appointments?
*"When can the AI book jobs?"*
→ e.g. *"Mon-Fri 8-6, Sat 9-12, closed Sun"*.
**Feeds:** `clients.business_hours` (column exists, wired at multi-client
build) + calendar slot window.

### 5. How far out should the AI book?
*"Same-day? Next-day? Two weeks?"*
→ e.g. *"within 7 days, same-day if before 2pm"*.
**Feeds:** scheduler slot range + prompt scheduling rule.

### 6. Who should the AI text when a job books, or an emergency hits?
*"What number do you want the booking/emergency alert on?"*
→ Confirm the owner's cell (usually the number we already have).
**Feeds:** `clients.owner_phone` (already collected at signup — confirm only).

### 7. Anything the AI should NEVER say or do?
*"Any no-nos? e.g. don't promise 24/7 service, don't mention competitors,
don't quote the trip fee, never say we're an AI."*
→ Free-text guardrails.
**Feeds:** prompt `rules` (append verbatim) — the safety rail.

### 8. How should the AI sound?
*"Casual like a friendly tech, or more professional/formal?"*
→ Default: casual (matches current personas).
**Feeds:** prompt `rules` tone line.

## Answers sheet (fill in, save as `onboarding/<client>.yaml`)

```yaml
client: Acme Plumbing LLC
business_type: plumbing            # or hvac / septic / new vertical
business_name: Acme Plumbing
service_area: "Helena, MT"
services: [clogs, water heaters, camera inspection]
emergency_triggers: [no water, gas smell, sewage backup]
pricing_policy: no_upfront_quotes  # or: flat_rate_menu
business_hours: "Mon-Fri 8-6, Sat 9-12, closed Sun"
booking_horizon_days: 7
owner_phone: "+14065551234"
guardrails: []
tone: casual
review_link: ""                    # fall back to .env if empty
```

## What this buys you

1. **Prompt YAML** — rendered from the answers (identity, objectives, rules,
   emergency line, pricing fork, tone).
2. **QA vocabulary** — `PERSONA_VOCAB` entry in `qa_dry_run.py` (issue =
   first service, emergency = first trigger, price_item = second service);
   the persona-driven suite then tests the client with THEIR words, not
   plumbing's.
3. **Client record** — `clients` table row (name, business_type, business_name,
   service_area, owner_phone, business_hours, review_link).
4. **Onboarding checklist** — questionnaire → answers YAML → prompt render →
   vocab entry → qa_dry_run pass → live E2E. No hours of scenario editing.

## Onboarding checklist (the whole client setup)

- [ ] Fill `onboarding/<client>.yaml` (questionnaire answers)
- [ ] Render `prompts/<vertical>.yaml` from answers
- [ ] Add `PERSONA_VOCAB` entry to `qa_dry_run.py`
- [ ] `python qa_dry_run.py --persona <client>` — must pass before live
- [ ] Insert client row (clients table)
- [ ] Client sets call forwarding (`sales/client-setup-guide.md`)
- [ ] Live E2E: call + text through the 406 number
