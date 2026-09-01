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


# Minimal delta over the original prompt, whose empty-coop behavior is
# proven correct (deployed service gates empty frames at chickens=0).
# The ONLY change from the original: the visibility rule for
# other_animals, which suppresses dusk phantom-predator reports.
# Never add species names here — an A/B on a lit EMPTY coop proved this
# model invents whatever animal it is told about (species menu -> "cat,
# dog, rat" 3/3; even "a rooster is never other" -> phantom rooster 2/2,
# while the plain old prompt says 0). Poultry reclassification is done
# in code (_reclassify_poultry), not words.
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
     '- other_animals = non-poultry animals clearly visible in the frame. '
     'Empty list if none. Only include an animal if most of its body is '
     'clearly visible; do not report suspected, hidden, or '
     'partially-glimpsed animals.\n'
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
    def __init__(self, base_url: str, *, timeout: float = 120.0,
                 allowed_species: list[str] | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._allowed = allowed_species if allowed_species is not None else ["*"]

    async def describe(self, jpeg: bytes, *, model: str) -> VisionResult:
        b64 = base64.b64encode(jpeg).decode("ascii")
        allowed = self._allowed
        
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

        return _build_result(data, model=model, latency_ms=latency_ms, allowed=allowed)


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


# Poultry terms the model files under other_animals when it splits the
# flock (production history: "2 chickens + 1 rooster" on a 3-bird coop).
# Folding them back into chickens prevents both the undercount (door
# would close early) and the false PREDATOR alert downstream. A frame
# the model split is treated as unreliable: confidence is clamped to
# 0.5 so the ESP32 consumer (conf >= 0.80 filter) drops it and waits
# for a clean frame. A prompt-side fix was tested and REJECTED: this
# model invents any animal it is told about, even in negation.
_POULTRY = (
    "rooster", "hen", "chick", "chicken", "pullet", "cockerel",
    "duck", "goose", "turkey", "fowl",
)


def _build_result(data: dict[str, Any], *, model: str, latency_ms: int,
                  allowed: list[str] | None = None) -> VisionResult:
    allowed = allowed if allowed is not None else ["*"]
    entries = _normalize_animals(data.get("other_animals"))
    chickens = _safe_int(data.get("chickens"), 0)
    confidence = _safe_float(data.get("confidence"), 0.0)
    notes = str(data.get("notes", ""))[:200]
    dropped: list[str] = []

    non_poultry = [sp for sp, _ in entries if not _is_poultry(sp)]
    poultry = [(sp, n) for sp, n in entries if _is_poultry(sp)]
    if poultry:
        if chickens > 0:
            # Flock split ("2 chickens + 1 rooster" on a 3-bird coop):
            # fold into the count and mark the frame unreliable (conf
            # clamp 0.5 -> consumer drops it, waits for a clean frame).
            folded = sum(n for _, n in poultry)
            chickens += folded
            confidence = min(confidence, 0.5)
            notes = (notes + f" [poultry folded into chickens: +{folded}]")[:200]
        else:
            # Phantom poultry on a birdless frame: drop entirely.
            # Poultry is never a predator, and a lone "duck" on an empty
            # coop is a hallucination, not a flock split.
            dropped.extend(sp for sp, _ in poultry)

    kept = non_poultry
    if "*" not in allowed:
        kept = [sp for sp in kept if _species_allowed(sp, allowed, dropped)]
    if dropped:
        log.warning("dropped implausible species (allowlist=%s): %s",
                    ",".join(allowed), ",".join(dropped))
    return VisionResult(
        chickens=chickens,
        other_animals=kept,
        movement=str(data.get("movement", "still")).lower(),
        confidence=confidence,
        notes=notes,
        model=model,
        latency_ms=latency_ms,
        raw=data,
    )


def _species_allowed(species: str, allowed: list[str], dropped: list[str]) -> bool:
    if any(a in species for a in allowed):
        return True
    dropped.append(species)
    return False


def _normalize_animals(v: Any) -> list[tuple[str, int]]:
    """Model returns either ["cat"] or [{"species": "cat", "count": 2}]."""
    if not isinstance(v, list):
        return []
    out: list[tuple[str, int]] = []
    for item in v:
        if isinstance(item, dict):
            sp = str(item.get("species", "")).lower().strip()
            if sp:
                out.append((sp, max(1, _safe_int(item.get("count", 1), 1))))
        else:
            sp = str(item).lower().strip()
            if sp:
                out.append((sp, 1))
    return out


def _is_poultry(species: str) -> bool:
    return any(p in species for p in _POULTRY)
