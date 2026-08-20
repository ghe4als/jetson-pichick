"""Frame capture sources.

Three implementations behind a single async iterator interface:

- SyntheticSource: cycles JPEGs in a directory. Useful before a real camera
  is attached; also great for unit tests and replay.
- V4L2Source: shells out to ``v4l2-ctl`` / ``ffmpeg`` to grab JPEGs from a
  USB UVC webcam at /dev/videoN.
- GStreamerSource: shells out to ``gst-launch-1.0`` with an
  ``nvarguscamerasrc`` pipeline for Jetson CSI cameras.

All three yield (jpeg_bytes, captured_at_monotonic) tuples. Capture errors
are logged but do not crash the iterator: it sleeps and retries.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from .config import Config

log = logging.getLogger(__name__)


def make_source(cfg: Config) -> "FrameSource":
    kind = cfg.capture_source.lower()
    if kind == "synthetic":
        return SyntheticSource(cfg)
    if kind == "v4l2":
        return V4L2Source(cfg)
    if kind == "gstreamer":
        return GStreamerSource(cfg)
    raise ValueError(f"unknown JCHICK_CAPTURE_SOURCE: {cfg.capture_source!r}")


class FrameSource:
    """Base interface for async frame iterators."""

    async def frames(self) -> AsyncIterator[tuple[bytes, float]]:  # pragma: no cover
        raise NotImplementedError
        yield  # type: ignore[unreachable]


class SyntheticSource(FrameSource):
    """Cycle JPEGs from a directory at the configured FPS.

    If the directory is empty or does not exist, a single procedurally
    generated frame is produced per tick (a numbered colored rectangle).
    Lets the full pipeline run end-to-end with no camera hardware.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._period = 1.0 / max(cfg.capture_fps, 0.01)
        self._dir = Path(cfg.synthetic_dir)

    async def frames(self) -> AsyncIterator[tuple[bytes, float]]:
        idx = 0
        while True:
            jpegs = sorted(self._dir.glob("*.jpg")) if self._dir.is_dir() else []
            if jpegs:
                jpeg = jpegs[idx % len(jpegs)].read_bytes()
            else:
                jpeg = self._fake_frame(idx)
            yield jpeg, time.monotonic()
            idx += 1
            await asyncio.sleep(self._period)

    def _fake_frame(self, idx: int) -> bytes:
        w, h = self._cfg.capture_width, self._cfg.capture_height
        # cycle through hues so frame-diff gate has work to do
        hue = (idx * 47) % 360
        img = Image.new("RGB", (w, h), _hsv_to_rgb(hue, 0.4, 0.7))
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), f"jchick synthetic frame {idx}", fill=(0, 0, 0))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


class V4L2Source(FrameSource):
    """Capture JPEGs from a USB UVC camera via ffmpeg.

    Requires ``ffmpeg`` to be installed (``apt install ffmpeg``). One ffmpeg
    invocation per frame is wasteful but trivial; if/when this matters we
    swap to a long-running ffmpeg piping MJPEG to stdout.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._period = 1.0 / max(cfg.capture_fps, 0.01)

    async def frames(self) -> AsyncIterator[tuple[bytes, float]]:
        while True:
            t0 = time.monotonic()
            try:
                jpeg = await self._grab_one()
                yield jpeg, time.monotonic()
            except Exception as e:
                log.warning("v4l2 capture failed: %s", e)
                await asyncio.sleep(1.0)
                continue
            elapsed = time.monotonic() - t0
            if elapsed < self._period:
                await asyncio.sleep(self._period - elapsed)

    async def _grab_one(self) -> bytes:
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-f", "v4l2",
            "-video_size", f"{self._cfg.capture_width}x{self._cfg.capture_height}",
            "-i", self._cfg.capture_device,
            "-frames:v", "1",
            "-pix_fmt", "yuvj420p",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "ffmpeg failed")
        if not stdout:
            raise RuntimeError("ffmpeg returned 0 bytes")
        return stdout


class GStreamerSource(FrameSource):
    """Capture JPEGs from a Jetson CSI camera via gst-launch.

    Uses ``nvarguscamerasrc`` so we get the Jetson ISP, not raw bayer. The
    pipeline encodes each frame to JPEG and writes a single file per tick;
    we read the file. Same shell-per-frame pattern as V4L2Source for now.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._period = 1.0 / max(cfg.capture_fps, 0.01)
        self._tmp = Path("/run/jchick-frame.jpg")

    async def frames(self) -> AsyncIterator[tuple[bytes, float]]:
        while True:
            t0 = time.monotonic()
            try:
                jpeg = await self._grab_one()
                yield jpeg, time.monotonic()
            except Exception as e:
                log.warning("gstreamer capture failed: %s", e)
                await asyncio.sleep(1.0)
                continue
            elapsed = time.monotonic() - t0
            if elapsed < self._period:
                await asyncio.sleep(self._period - elapsed)

    async def _grab_one(self) -> bytes:
        pipeline = (
            f"nvarguscamerasrc num-buffers=1 ! "
            f"video/x-raw(memory:NVMM),width={self._cfg.capture_width},"
            f"height={self._cfg.capture_height},framerate=30/1 ! "
            f"nvjpegenc ! filesink location={self._tmp}"
        )
        proc = await asyncio.create_subprocess_exec(
            "gst-launch-1.0", "-q", *pipeline.split(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "gst-launch failed")
        return self._tmp.read_bytes()
