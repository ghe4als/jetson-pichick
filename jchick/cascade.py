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
        try:
            detail = await self._client.describe(jpeg, model=self._detail_model)
        except OllamaError as e:
            log.warning("detail model %s failed: %s", self._detail_model, e)
            return CascadeResult(gate=gate, detail=None, fired=True)
        return CascadeResult(gate=gate, detail=detail, fired=True)


def _gate_says_interesting(r: VisionResult) -> bool:
    """Decide whether the gate output is worth a detail-model pass."""
    if r.chickens > 0:
        return True
    if r.other_animals:
        return True
    if r.movement == "active":
        return True
    return False
