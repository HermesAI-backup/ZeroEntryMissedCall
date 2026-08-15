"""OpenAI-compatible LLM client (works with OpenAI, OpenRouter, Groq, etc.)."""

from __future__ import annotations

from typing import AsyncGenerator

import httpx

from config import get_settings

settings = get_settings()


class LLMClient:
    """Thin async wrapper around OpenAI-compatible chat completions API."""

    def __init__(self):
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 500,
        response_format: dict | None = None,
    ) -> str:
        """Send messages to the LLM and return the response text."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

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
        import json

        # Try to find JSON object in the response
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fallback: find first { ... } block
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise
