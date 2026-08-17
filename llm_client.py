"""OpenAI-compatible LLM client with retry + fallback chain.

Tier 1: primary provider (LLM_BASE_URL / LLM_MODEL)   — deepseek via Hermes proxy
Tier 2: fallback model on the same base URL            — e.g. openai/gpt-4.1-mini
Tier 3: local Ollama (LLM_FALLBACK2_BASE_URL)          — degraded but always available

Any bearer token works on the Hermes proxy; Ollama needs no auth.
Handles reasoning models that return content=None (token budget eaten by
`reasoning`) by retrying once with a 3x token budget before failing over.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator  # kept for backwards compat

import httpx

from config import get_settings

logger = logging.getLogger("llm-client")

settings = get_settings()


class LLMClient:
    """Thin async wrapper around OpenAI-compatible chat completions API."""

    def __init__(self):
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.fallback_model = settings.llm_fallback_model  # same base URL (e.g. openai/gpt-4.1-mini)
        self.local_base_url = settings.llm_fallback2_base_url.rstrip("/")
        self.local_model = settings.llm_fallback2_model
        self.local_api_key = settings.llm_fallback2_api_key

    def _is_local(self, base_url: str) -> bool:
        return "11434" in base_url

    def _headers(self, base_url: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if not self._is_local(base_url):
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 500,
        response_format: dict | None = None,
    ) -> str:
        """Send messages to the LLM and return the response text.

        Retries with a bigger token budget on empty content (reasoning-model
        quirk), then fails over through the fallback chain.
        """
        last_err: Exception | None = None
        attempts = [
            ("primary", self.base_url, self.model),
            ("fallback", self.base_url, self.fallback_model),
            ("local", self.local_base_url, self.local_model),
        ]
        for tier, base_url, model in attempts:
            if not model:
                continue
            try:
                text = await self._chat_once(
                    base_url, model, messages, temperature, max_tokens, response_format
                )
                if text:
                    if tier != "primary":
                        logger.warning("LLM served by %s tier: %s", tier, model)
                    return text
                last_err = RuntimeError(f"{tier} returned empty content")
                logger.warning("LLM %s tier (%s) returned empty content", tier, model)
            except Exception as e:
                last_err = e
                logger.warning("LLM %s tier (%s) failed: %s", tier, model, e)
        raise RuntimeError(f"All LLM tiers failed: {last_err}")

    async def _chat_once(
        self,
        base_url: str,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_format: dict | None,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            # Ollama's OpenAI-compat doesn't reliably support json_object — drop it there
            if not self._is_local(base_url):
                payload["response_format"] = response_format
        headers = self._headers(base_url)

        # Attempt 1: normal budget. Attempt 2: 3x budget — reasoning models
        # burn tokens on `reasoning` and return content=None when truncated.
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=90.0) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions", headers=headers, json=payload
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
                    if content and content.strip():
                        return content.strip()
                    # Empty content — bump budget and retry once
                    payload["max_tokens"] = max(payload.get("max_tokens", 500) * 3, 3000)
            except Exception:
                if attempt == 0:
                    payload["max_tokens"] = max(payload.get("max_tokens", 500) * 3, 3000)
                    continue
                raise
        return ""

    async def chat_structured(
        self,
        messages: list[dict],
        json_schema: dict,
        temperature: float = 0.3,
    ) -> dict:
        """Send messages expecting a structured JSON response."""
        text = await self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fallback: find first { ... } block
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise
