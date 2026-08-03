"""Pre-LLM frame-delta gate.

Compares the current frame against the last frame using mean absolute
pixel difference on a small grayscale thumbnail. If the score is below
threshold the frame is "uninteresting" and skipped.

Threshold semantics: 0..1, where 1.0 is the maximum possible mean abs
pixel diff (255/255). 0.015 means roughly "1.5% of saturation has
changed" which empirically picks up subjects entering the scene without
firing on every breeze.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image

THUMBNAIL = (64, 64)


class FrameDiffGate:
    def __init__(self, threshold: float = 0.015, warmup: int = 3) -> None:
        self._threshold = max(0.0, threshold)
        self._warmup = max(0, warmup)
        self._prev: list[int] | None = None
        self._seen = 0

    def evaluate(self, jpeg: bytes) -> tuple[bool, float]:
        """Return (keep_frame, diff_score). Always retain warmup frames."""
        cur = self._thumb(jpeg)
        if self._seen < self._warmup or self._prev is None:
            self._prev = cur
            self._seen += 1
            return True, 1.0
        score = _mean_abs_diff(self._prev, cur)
        self._prev = cur
        return score >= self._threshold, score

    def _thumb(self, jpeg: bytes) -> list[int]:
        with Image.open(BytesIO(jpeg)) as img:
            small = img.convert("L").resize(THUMBNAIL, Image.Resampling.BILINEAR)
            return list(small.getdata())


def _mean_abs_diff(a: list[int], b: list[int]) -> float:
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    total = 0
    for x, y in zip(a, b):
        total += abs(x - y)
    return (total / n) / 255.0
