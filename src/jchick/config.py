"""Runtime configuration for jetson-pichick.

Loads from os.environ. Every var has a sane default except NATS_URL and
OLLAMA_URL: those must point at real services or the app refuses to start.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass


def _bool(s: str, default: bool = False) -> bool:
    if s is None or s == "":
        return default
    return s.strip().lower() in ("1", "true", "yes", "on")


def _species_list(raw: str | None) -> list[str]:
    """Comma-separated species allowlist; '*' or empty disables filtering."""
    if raw is None or raw.strip() in ("", "*"):
        return ["*"]
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


@dataclass(frozen=True)
class Config:
    # connectivity
    nats_url: str
    ollama_url: str

    # models
    gate_model: str           # cheap "is anything alive" classifier
    detail_model: str         # higher-fidelity model run only when gate fires

    # capture
    capture_source: str       # synthetic | v4l2 | gstreamer
    capture_device: str       # /dev/video0 (v4l2) or full pipeline (gstreamer)
    capture_fps: float        # frames per second to sample
    capture_width: int
    capture_height: int
    synthetic_dir: str        # directory of test JPEGs cycled by synthetic source

    # gating
    diff_threshold: float     # 0..1, mean abs pixel delta required to keep frame
    diff_warmup_frames: int   # always inference the first N frames (no diffing)

    # detection policy
    allowed_species: list[str]  # other_animals allowlist; ["*"] disables filtering

    # publishing
    host: str                 # used in subject prefix home.coop.<host>.*
    heartbeat_seconds: float
    tegrastats_seconds: float

    # web viewer
    http_port: int            # 0 = disabled; 8090 = default stream port

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = env if env is not None else os.environ

        missing = [k for k in ("NATS_URL", "OLLAMA_URL") if not e.get(k)]
        if missing:
            raise ValueError(f"missing required env vars: {missing}")

        return cls(
            nats_url=e["NATS_URL"],
            ollama_url=e["OLLAMA_URL"].rstrip("/"),
            gate_model=e.get("JCHICK_GATE_MODEL", "moondream:1.8b"),
            detail_model=e.get("JCHICK_DETAIL_MODEL", "llava:7b"),
            capture_source=e.get("JCHICK_CAPTURE_SOURCE", "synthetic"),
            capture_device=e.get("JCHICK_CAPTURE_DEVICE", "/dev/video0"),
            capture_fps=float(e.get("JCHICK_CAPTURE_FPS", "1.0")),
            capture_width=int(e.get("JCHICK_CAPTURE_WIDTH", "1280")),
            capture_height=int(e.get("JCHICK_CAPTURE_HEIGHT", "720")),
            synthetic_dir=e.get("JCHICK_SYNTHETIC_DIR", "/var/lib/jchick/synthetic"),
            diff_threshold=float(e.get("JCHICK_DIFF_THRESHOLD", "0.015")),
            diff_warmup_frames=int(e.get("JCHICK_DIFF_WARMUP", "3")),
            allowed_species=_species_list(e.get("JCHICK_ALLOWED_SPECIES", "human,mouse,mice,rat,rats")),
            host=e.get("JCHICK_HOST", socket.gethostname()),
            heartbeat_seconds=float(e.get("JCHICK_HEARTBEAT_SECONDS", "300")),
            tegrastats_seconds=float(e.get("JCHICK_TEGRASTATS_SECONDS", "60")),
            http_port=int(e.get("JCHICK_HTTP_PORT", "0")),
        )

    def subject(self, suffix: str) -> str:
        return f"home.coop.{self.host}.{suffix}"
