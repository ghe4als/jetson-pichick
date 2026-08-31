"""Two-stage gated inference.

Stage 1: cheap ``gate_model`` (default moondream:1.8b) decides whether the
frame contains anything live. If yes -> Stage 2.

Stage 2: higher-fidelity ``detail_model`` (default llava:7b) re-inferences
the same frame for the count + species we publish.

Cost model on Orin Nano (rough): moondream ~0.5s, llava:7b ~3-6s. If only
~10% of frames have animals, cascade is ~5x faster on average than
running llava on every frame, with no loss of detection quality on the
hits.

Returned ``CascadeResult`` carries both stages' outputs so callers can
log the gate's decision separately from the final answer.

Same-model shortcut: when ``gate_model == detail_model`` (the current
deployment runs llava-phi3:3.8b for both), the gate pass already IS the
detail pass, so ``run`` reuses it instead of calling Ollama twice.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .ollama import OllamaClient, OllamaError, VisionResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CascadeResult:
    gate: VisionResult
    detail: VisionResult | None
    fired: bool


class Cascade:
    def __init__(
        self,
        client: OllamaClient,
        *,
        gate_model: str,
        detail_model: str,
    ) -> None:
        self._client = client
        self._gate_model = gate_model
        self._detail_model = detail_model

    async def run(self, jpeg: bytes) -> CascadeResult:
        gate = await self._client.describe(jpeg, model=self._gate_model)
        if not _gate_says_interesting(gate):
            return CascadeResult(gate=gate, detail=None, fired=False)
        if self._gate_model == self._detail_model:
            # Same model for both stages: the gate pass already answered
            # the detail question with the identical model, prompt, and
            # frame. Re-running it only adds latency (production history:
            # the two passes disagreed on 3 of 573 frames), so reuse it.
            return CascadeResult(gate=gate, detail=gate, fired=True)
        try:
            detail = await self._client.describe(jpeg, model=self._detail_model)
        except OllamaError as e:
            log.warning("detail model %s failed: %s", self._detail_model, e)
            return CascadeResult(gate=gate, detail=None, fired=True)
        return CascadeResult(gate=gate, detail=detail, fired=True)


def _gate_says_interesting(r: VisionResult) -> bool:
    """Decide whether the gate output is worth a detail-model pass.

    Fires only when animals are actually detected. We deliberately do NOT
    fire on movement=="active" alone — the gate model judges motion from a
    single still frame, which is unreliable and caused false fires on
    static-but-noisy frames that leaked through the pixel-diff gate. The
    diff gate upstream is the real motion detector; the gate model's job
    is to identify what's present, not whether it's moving.
    """
    if r.chickens > 0:
        return True
    if r.other_animals:
        return True
    return False
