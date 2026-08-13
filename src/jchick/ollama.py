"""Async Ollama client.

Single method: describe(jpeg, model, prompt) -> structured dict.

Uses format=json so the model returns parsed JSON directly — no
natural-language regex parsing the way piChick v1 did. The schema we ask
for is intentionally tiny and identical for every model so cascade-mode
results are comparable apples-to-apples.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


PROMPT = (
     'You are a chicken-coop security camera analyzing one still frame. '
     'Reply ONLY with one JSON object on a single line, matching this '
     'schema. No prose. No code fence. No trailing text. No newlines in '
     'strings.\n'
     'Schema: {"chickens": <int>, "other_animals": [<lowercase species>], '
     '"movement": "still"|"calm"|"active", "confidence": <float 0..1>, '
     '"notes": "<one short sentence>"}\n'
     'Rules:\n'
     '- chickens = number of chickens VISIBLE in the frame. If no chickens '
     'are visible, use 0. Do not assume chickens are present.\n'
     '- other_animals = list of other animal species clearly visible in the '
     'frame. Empty list if none.\n'
     '- If the frame shows no animals at all, return exactly: '
     '{"chickens": 0, "other_animals": [], "movement": "still", '
     '"confidence": 0.9, "notes": "No animals visible."}\n'
     '- confidence = how sure you are animals are actually present, 0..1. '
     'Use under 0.5 if unclear, dark, or motion-blurred.\n'
     '- Do not invent animals. An empty coop is the expected, correct '
     'answer when no animals are visible.'
)


@dataclass(frozen=True)
class VisionResult:
    chickens: int
    other_animals: list[str]
    movement: str
    confidence: float
    notes: str
    model: str
    latency_ms: int
    raw: dict[str, Any]


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, *, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def describe(self, jpeg: bytes, *, model: str) -> VisionResult:
        b64 = base64.b64encode(jpeg).decode("ascii")
        
        # Use /api/chat endpoint which properly supports format=json
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": f"System: {PROMPT}"},
                {"role": "user", "content": "Analyze this image.", "images": [b64]},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 256,
                "num_ctx": 2048,
            },
        }
        
        url = f"{self._base}/api/chat"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama transport error: {e}") from e

        if resp.status_code != 200:
            raise OllamaError(
                f"ollama HTTP {resp.status_code}: {resp.text[:200]}"
             )

        try:
            envelope = resp.json()
        except json.JSONDecodeError as e:
            raise OllamaError(f"ollama returned non-JSON envelope: {e}") from e

        latency_ms = int(envelope.get("total_duration", 0) / 1_000_000)
        
        # Handle multiple message formats: /api/chat returns message.content
        message = envelope.get("message")
        if message is None:
            message = envelope.get("error", "")
            raise OllamaError(message)
        
        text = message.get("content", "")
        
        # If we got streaming response, join chunks
        if isinstance(text, list):
            text = "".join(text)
        
        # Clean up any markdown fence artifacts
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            log.warning("model %s did not return valid JSON: %s", model, text[:500])
            raise OllamaError(f"model returned non-JSON: {text[:500]}...") from e

        return VisionResult(
            chickens=_safe_int(data.get("chickens"), 0),
            other_animals=_safe_str_list(data.get("other_animals")),
            movement=str(data.get("movement", "still")).lower(),
            confidence=_safe_float(data.get("confidence"), 0.0),
            notes=str(data.get("notes", ""))[:200],
            model=model,
            latency_ms=latency_ms,
            raw=data,
         )


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).lower().strip() for x in v if str(x).strip()]
