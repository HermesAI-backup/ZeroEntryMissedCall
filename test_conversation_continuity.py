"""Regression tests: conversation continuity fixes (2026-08-17).

Three bugs from the live plumbing E2E post-mortem:
  1. Service personas (plumbing/hvac/septic) classified as `hot_lead` — a
     sales-only concept — because the default branch definitions included it.
     A customer asking price/timing is booking-intent, NOT a lead.
  2. ANY branch completed the conversation (app.py line ~566). The customer's
     next text found no active conversation → auto-start → amnesia ("I already
     told you everything" loop).
  3. Auto-start seeded empty metadata + the "sorry I missed your call" greeting
     with zero carry-over from the prior conversation.

Guards:
  - _get_branch_definitions("plumbing") must NOT mention hot_lead
  - _allowed_branches_json("plumbing") must not offer hot_lead; sales may
  - evaluate_branch must coerce hot_lead → none for service personas
    (via a stubbed LLM)
  - app.py source: only branch == "booked" sets state = "completed"
  - app.py source: auto-start carries prior metadata (CARRY_KEYS) and adds a
    context message instead of the greeting when context exists

Usage: python test_conversation_continuity.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

from branching import _allowed_branches_json, _get_branch_definitions  # noqa: E402
from branching import evaluate_branch  # noqa: E402

APP_SRC = (PROJECT / "app.py").read_text(encoding="utf-8")
BRANCH_SRC = (PROJECT / "branching.py").read_text(encoding="utf-8")


def test_service_definitions_have_no_hot_lead() -> None:
    for persona in ("plumbing", "hvac", "septic", "default"):
        defs = _get_branch_definitions(persona)
        assert "hot_lead" not in defs, f"{persona} definitions still mention hot_lead"


def test_sales_definitions_keep_hot_lead() -> None:
    assert "hot_lead" in _get_branch_definitions("sales")


def test_allowed_branches_json_per_persona() -> None:
    assert "hot_lead" not in _allowed_branches_json("plumbing")
    assert "hot_lead" not in _allowed_branches_json("hvac")
    assert "hot_lead" in _allowed_branches_json("sales")


async def _stub_hot_lead_eval() -> None:
    """Patch LLMClient.chat_structured to return hot_lead, then assert coerce."""
    import llm_client

    orig = llm_client.LLMClient.chat_structured

    async def fake(self, messages, json_schema, temperature=0.3):
        return {"branch": "hot_lead", "reason": "interested in pricing"}

    llm_client.LLMClient.chat_structured = fake  # type: ignore[assignment]
    try:
        branch, _ = await evaluate_branch(
            [{"role": "user", "content": "what's the price?"}], "plumbing"
        )
        assert branch == "none", f"plumbing hot_lead should coerce to none, got {branch}"
        branch_sales, _ = await evaluate_branch(
            [{"role": "user", "content": "I want to sign up, call me"}], "sales"
        )
        assert branch_sales == "hot_lead", f"sales hot_lead should pass through, got {branch_sales}"
    finally:
        llm_client.LLMClient.chat_structured = orig


def test_hot_lead_coerced_for_service_personas() -> None:
    import asyncio

    asyncio.run(_stub_hot_lead_eval())


async def _stub_emergency_evals() -> None:
    """Patch LLMClient.chat_structured to return emergency, then assert the
    deterministic evidence gate (2026-08-18 adversarial finding): the LLM
    over-triggers emergency on emoji/tone; without customer evidence the
    branch must coerce to none (the owner SMS is the expensive path)."""
    import llm_client

    orig = llm_client.LLMClient.chat_structured

    async def fake(self, messages, json_schema, temperature=0.3):
        return {"branch": "emergency", "reason": "customer sounds urgent"}

    llm_client.LLMClient.chat_structured = fake  # type: ignore[assignment]
    try:
        # Emoji/tone only — must coerce to none (no owner alert fired)
        branch, _ = await evaluate_branch(
            [{"role": "user", "content": "🔥🚨💧💦"}], "plumbing"
        )
        assert branch == "none", f"emoji should NOT trigger emergency, got {branch}"
        # All-caps urgency without a real hazard — must coerce to none
        branch2, _ = await evaluate_branch(
            [{"role": "user", "content": "I NEED A PLUMBER RIGHT NOW!!!!"}], "plumbing"
        )
        assert branch2 == "none", f"all-caps should NOT trigger emergency, got {branch2}"
        # REAL emergency words — must pass through
        branch3, _ = await evaluate_branch(
            [{"role": "user", "content": "my basement is flooding RIGHT NOW!!"}], "plumbing"
        )
        assert branch3 == "emergency", f"flooding should trigger emergency, got {branch3}"
        branch4, _ = await evaluate_branch(
            [{"role": "user", "content": "I smell gas in the house"}], "plumbing"
        )
        assert branch4 == "emergency", f"gas smell should trigger emergency, got {branch4}"
    finally:
        llm_client.LLMClient.chat_structured = orig


def test_emergency_evidence_gate() -> None:
    import asyncio

    asyncio.run(_stub_emergency_evals())


def test_only_booked_completes_conversation() -> None:
    # The branch-handling block is the one right before the "branched to" log.
    idx = APP_SRC.index("branched to '%s'")
    block_start = APP_SRC.rindex("if branch == \"booked\":", 0, idx)
    block = APP_SRC[block_start:idx]
    # Within the branch-handling block, completion is conditional on booked
    assert 'conversation.state = "completed"' in block
    # And there must NOT be an unconditional completion between the branch
    # detection and the log — i.e. no `conversation.state = "completed"` line
    # at the same indent as the `if branch and branch != "none":` check.
    assert "ONLY a confirmed booking completes" in APP_SRC


def test_auto_start_carries_prior_metadata() -> None:
    assert "CARRY_KEYS" in APP_SRC
    assert "prior.metadata_json" in APP_SRC
    assert "Never re-greet someone mid-booking" in APP_SRC
    assert "Continuing an earlier conversation" in APP_SRC
    # The greeting must NOT be added when context is carried
    assert "if carried:" in APP_SRC
    assert "else:" in APP_SRC


def test_response_count_zero_when_carried() -> None:
    assert "response_count=0 if carried else 1" in APP_SRC


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASS: {len(tests)} conversation-continuity checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
