"""Branching logic — evaluates conversation against booking/emergency/unqualified conditions."""

from __future__ import annotations

from typing import Literal

BranchName = Literal["booked", "emergency", "unqualified", "hot_lead", "none"]


async def evaluate_branch(
    conversation_history: list[dict],
    business_type: str,
) -> tuple[BranchName, str | None]:
    """Use LLM to classify where the conversation stands."""
    # Import here to avoid circular deps
    from llm_client import LLMClient

    branch_definitions = _get_branch_definitions(business_type)
    history_text = _format_history(conversation_history)

    prompt = f"""You are a conversation classifier for a {business_type} business's missed-call AI agent.

Read the conversation below and decide which branch it falls into. Be conservative — only classify if the customer has clearly triggered one of these conditions.

BRANCH DEFINITIONS:
{branch_definitions}

CONVERSATION:
{history_text}

Respond in JSON:
{{"branch": "booked" | "emergency" | "unqualified" | "hot_lead" | "none", "reason": "brief explanation"}}"""

    llm = LLMClient()
    try:
        result = await llm.chat_structured(
            messages=[{"role": "user", "content": prompt}],
            json_schema={"type": "object", "properties": {"branch": {"type": "string"}, "reason": {"type": "string"}}},
        )
        branch = result.get("branch", "none")
        if branch not in ("booked", "emergency", "unqualified", "hot_lead", "none"):
            branch = "none"
        return branch, result.get("reason")
    except Exception:
        return "none", None


def _get_branch_definitions(business_type: str) -> str:
    """Return branch trigger definitions for the given business type."""
    definitions = {
        "default": """
- booked: Customer has provided their service need (what they need help with), their address/location, and a preferred day/time for an estimate or appointment.
- emergency: Customer mentioned an urgent or dangerous situation — property damage, safety hazard, flood, fire, gas leak, tree on house, etc. — that requires immediate help.
- unqualified: Customer is asking about services the business doesn't offer, is outside the service area, is clearly not interested, or is a wrong number.
- hot_lead: Customer is very interested, asking detailed questions about pricing, wants to start immediately, or asks for a demo/call.
- none: None of the above conditions are clearly met yet.
""",
    }
    return definitions.get(business_type, definitions["default"])


def _format_history(messages: list[dict]) -> str:
    """Format message history for the LLM."""
    lines = []
    for m in messages:
        role_label = "Customer" if m["role"] == "user" else "AI Agent"
        lines.append(f"{role_label}: {m['content']}")
    return "\n".join(lines)
