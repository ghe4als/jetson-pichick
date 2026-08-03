"""Main async event loop.

Three concurrent tasks gathered under a single stop event:

1. inference_loop: pulls frames from the configured FrameSource, runs the
   diff gate, then the cascade (gate model -> detail model on hits),
   publishes results to NATS as they happen.
2. heartbeat_loop: every JCHICK_HEARTBEAT_SECONDS publishes a status
   message so subscribers know the node is alive and what its last seen
   counts were, even when nothing interesting is happening.
3. tegra_loop: long-running ``tegrastats`` subprocess, reads one line per
   JCHICK_TEGRASTATS_SECONDS, publishes parsed sample.

Single asyncio.Event flips on SIGTERM/SIGINT; every loop checks it on
its way around.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .capture import make_source
from .cascade import Cascade, CascadeResult
from .config import Config
from .diff import FrameDiffGate
from .nats_pub import NatsPublisher
from .ollama import OllamaClient, OllamaError
from .tegra import parse_line as parse_tegra_line

log = logging.getLogger(__name__)


class App:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._stop = asyncio.Event()
        self._source = make_source(cfg)
        self._diff = FrameDiffGate(
            threshold=cfg.diff_threshold,
            warmup=cfg.diff_warmup_frames,
        )
        self._client = OllamaClient(cfg.ollama_url)
        self._cascade = Cascade(
            self._client,
            gate_model=cfg.gate_model,
            detail_model=cfg.detail_model,
        )
        self._pub = NatsPublisher(cfg.nats_url)
        self._last_chickens: int = 0
        self._frames_seen = 0
        self._frames_inferenced = 0
        self._frames_fired = 0

    async def run(self) -> None:
        # Start NATS in the background — don't block the event loop on the
        # initial connect. The broker may be unreachable at startup (WiFi
        # outage, broker still booting); the inference loop should still
        # run, frames should still hit Ollama, and the publisher will
        # quietly catch up once the broker comes back. Without this, an
        # unreachable broker silently wedges the whole service before the
        # capture/inference/tegra tasks ever spawn.
        asyncio.create_task(self._pub.start(), name="nats_connect")
        await self._publish_startup()
        tasks = [
            asyncio.create_task(self._inference_loop(), name="inference"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._tegra_loop(), name="tegra"),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._publish_shutdown()
            await self._pub.stop()

    def stop(self) -> None:
        self._stop.set()

    # ---- loops ------------------------------------------------------

    async def _inference_loop(self) -> None:
        try:
            async for jpeg, _captured_at in self._source.frames():
                if self._stop.is_set():
                    break
                self._frames_seen += 1
                keep, score = self._diff.evaluate(jpeg)
                if not keep:
                    log.debug("diff gate skipped frame (score=%.4f)", score)
                    continue
                self._frames_inferenced += 1
                try:
                    result = await self._cascade.run(jpeg)
                except OllamaError as e:
                    log.warning("inference failed: %s", e)
                    await self._publish("alert.ollama", {"error": str(e)})
                    continue
                if result.fired:
                    self._frames_fired += 1
                await self._publish_inference(result, diff_score=score)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("inference_loop crashed")

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            await self._sleep(self._cfg.heartbeat_seconds)
            if self._stop.is_set():
                break
            await self._publish("status.heartbeat", {
                "frames_seen": self._frames_seen,
                "frames_inferenced": self._frames_inferenced,
                "frames_fired": self._frames_fired,
                "last_chickens": self._last_chickens,
            })

    async def _tegra_loop(self) -> None:
        # tegrastats interval is in milliseconds; we want one line per
        # tegrastats_seconds. Fall through quietly if tegrastats missing.
        ms = max(int(self._cfg.tegrastats_seconds * 1000), 1000)
        try:
            proc = await asyncio.create_subprocess_exec(
                "tegrastats", "--interval", str(ms),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.info("tegrastats not present; skipping GPU telemetry loop")
            return
        try:
            assert proc.stdout is not None
            while not self._stop.is_set():
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                sample = parse_tegra_line(line_bytes.decode(errors="replace"))
                if sample:
                    await self._publish("status.tegra", sample)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass

    # ---- publish helpers --------------------------------------------

    async def _publish(self, suffix: str, payload: dict[str, Any]) -> None:
        payload = {
            "host": self._cfg.host,
            "ts": _now_iso(),
            **payload,
        }
        await self._pub.publish(self._cfg.subject(suffix), payload)

    async def _publish_startup(self) -> None:
        await self._publish("status.startup", {
            "event": "startup",
            "version": __version__,
            "gate_model": self._cfg.gate_model,
            "detail_model": self._cfg.detail_model,
            "capture_source": self._cfg.capture_source,
            "capture_fps": self._cfg.capture_fps,
        })

    async def _publish_shutdown(self) -> None:
        try:
            await self._publish("status.shutdown", {
                "event": "shutdown",
                "frames_seen": self._frames_seen,
                "frames_inferenced": self._frames_inferenced,
                "frames_fired": self._frames_fired,
            })
        except Exception as e:
            log.debug("shutdown publish failed: %s", e)

    async def _publish_inference(self, r: CascadeResult, *, diff_score: float) -> None:
        chosen = r.detail or r.gate
        self._last_chickens = chosen.chickens
        gate_payload = {
            "chickens": r.gate.chickens,
            "other_animals": r.gate.other_animals,
            "movement": r.gate.movement,
            "confidence": r.gate.confidence,
            "model": r.gate.model,
            "latency_ms": r.gate.latency_ms,
        }
        if not r.fired:
            await self._publish("inference.gated", {
                "diff_score": round(diff_score, 4),
                "gate": gate_payload,
            })
            return
        detail_payload = None
        if r.detail is not None:
            detail_payload = {
                "chickens": r.detail.chickens,
                "other_animals": r.detail.other_animals,
                "movement": r.detail.movement,
                "confidence": r.detail.confidence,
                "notes": r.detail.notes,
                "model": r.detail.model,
                "latency_ms": r.detail.latency_ms,
            }
        await self._publish("inference.fired", {
            "diff_score": round(diff_score, 4),
            "gate": gate_payload,
            "detail": detail_payload,
            "chickens": chosen.chickens,
        })
        if chosen.chickens > 0:
            await self._publish("detection.chicken", {
                "count": chosen.chickens,
                "confidence": chosen.confidence,
                "notes": chosen.notes,
                "model": chosen.model,
            })
        for sp in chosen.other_animals:
            await self._publish(f"detection.{_subject_safe(sp)}", {
                "class": sp,
                "confidence": chosen.confidence,
                "notes": chosen.notes,
                "model": chosen.model,
            })

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _subject_safe(s: str) -> str:
    """NATS subjects can't contain spaces or dots."""
    return "".join(c if c.isalnum() else "_" for c in s.strip().lower()) or "unknown"


async def run(cfg: Config) -> None:
    app = App(cfg)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, app.stop)
        except NotImplementedError:
            pass
    log.info(
        "jchick: starting host=%s ollama=%s nats=%s gate=%s detail=%s capture=%s@%.2ffps",
        cfg.host, cfg.ollama_url, cfg.nats_url,
        cfg.gate_model, cfg.detail_model,
        cfg.capture_source, cfg.capture_fps,
    )
    t0 = time.monotonic()
    try:
        await app.run()
    finally:
        log.info("jchick: stopped after %.1fs", time.monotonic() - t0)
