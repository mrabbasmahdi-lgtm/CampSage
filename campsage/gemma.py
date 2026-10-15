"""Central Gemini/Gemma adapter with per-call keys and fallback cooldowns."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelResult:
    text: str
    model_id: str


def classify_error(error: Exception) -> str:
    message = str(error).lower()
    if "429" in message or "resource_exhausted" in message or "quota" in message:
        return "quota"
    if any(value in message for value in ("503", "504", "unavailable", "deadline_exceeded", "timeout")):
        return "unavailable"
    if any(value in message for value in ("403", "404", "not found", "not supported", "permission", "api key not valid")):
        return "invalid"
    return "error"


class GemmaClient:
    def __init__(self, model_id: str, fallback_model_ids: tuple[str, ...] = (), timeout_ms: int = 30_000):
        self.model_ids = tuple(dict.fromkeys(value for value in (model_id, *fallback_model_ids) if value))
        self.timeout_ms = max(10_000, timeout_ms)
        self._cooldown_until: dict[str, float] = {}

    def _client(self, api_key: str):
        from google import genai
        from google.genai import types

        return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=self.timeout_ms))

    def validate_key(self, api_key: str) -> ModelResult:
        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        return self._generate(api_key, "Reply with the single word ok.", (self.model_ids[0],))

    def _generate(self, api_key: str, prompt: str, model_ids: tuple[str, ...]) -> ModelResult:
        if not model_ids:
            raise RuntimeError("no model is configured")
        last_error: Exception | None = None
        for model_id in model_ids:
            if time.monotonic() < self._cooldown_until.get(model_id, 0):
                continue
            try:
                response = self._client(api_key).models.generate_content(model=model_id, contents=prompt)
                return ModelResult((response.text or "").strip(), model_id)
            except Exception as exc:  # SDK errors vary by provider version.
                last_error = exc
                category = classify_error(exc)
                self._cooldown_until[model_id] = time.monotonic() + (300 if category == "quota" else 60)
                if category == "invalid":
                    break
        raise last_error or RuntimeError("all configured models are cooling down")

    def generate(self, api_key: str, prompt: str) -> ModelResult:
        return self._generate(api_key, prompt, self.model_ids)
