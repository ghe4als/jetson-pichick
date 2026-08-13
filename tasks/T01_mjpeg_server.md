# T01 — Port mjpeg_server.py into src/jchick/ with overlay + last-result store

## Goal
Create `src/jchick/mjpeg_server.py` that serves a multipart/x-mixed-replace
MJPEG stream with a text HUD overlay (chicken count, confidence, movement,
gate/detail labels, diff score, fps, timestamp), plus a shared
`LastResult` store the inference loop writes and the stream loop reads.

## Context
`jchick/mjpeg_server.py` (stale tree) has the basic stream working but:
- serves raw frames, no overlay
- no shared detection state
- has the `cfg.http_port` bug elsewhere (not in this file)

We port it to `src/jchick/mjpeg_server.py`, add overlay, add
`LastResult` dataclass and `update_last_result()` method. The wiring
into app.py is T03 (separate task).

## Files touched
- `src/jchick/mjpeg_server.py` — NEW file (created from scratch, not copied)

## Pre-conditions
- [ ] none

## Exact changes required

### Change 1: Create src/jchick/mjpeg_server.py

File: `src/jchick/mjpeg_server.py`
Action: CREATE

Contents (full file):

```python
"""Simple MJPEG streaming server with detection overlay.

Provides:
- /stream endpoint serving multipart/x-mixed-replace video stream
- / endpoint serving a simple HTML viewer page
- LastResult shared state: inference loop writes, stream loop reads
- Text HUD overlay (chicken count, confidence, movement, diff score, fps,
  timestamp). No bounding boxes — the Ollama prompt doesn't return them.

Uses Python stdlib only - no external dependencies beyond PIL (already
in pyproject.toml) for JPEG decode + ImageDraw overlay.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from .capture import FrameSource
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class LastResult:
    """Shared state between inference loop (writer) and stream loop (reader).

    No lock needed — single asyncio event loop, no threads. The inference
    loop calls MJPEGServer.update_last_result(); the stream loop reads
    self._last on each frame. Stale reads are fine (overlay just shows
    the previous frame's detection).
    """
    chickens: int = 0
    other_animals: list[str] = field(default_factory=list)
    movement: str = "still"
    confidence: float = 0.0
    notes: str = ""
    model: str = ""
    latency_ms: int = 0
    fired: bool = False
    diff_score: float = 0.0
    frames_seen: int = 0
    frames_inferenced: int = 0
    frames_fired: int = 0
    updated_at: float = 0.0  # monotonic


class MJPEGServer:
    """Simple async MJPEG streaming server using HTTP 1.1 keepalive.

    Serves:
    - GET / - HTML viewer page
    - GET /stream - MJPEG stream (multipart/x-mixed-replace) with HUD overlay
    """

    def __init__(self, cfg: Config, capture_source: FrameSource) -> None:
        self._cfg = cfg
        self._source = capture_source
        self._port = cfg.http_port
        self._server: asyncio.base_server.Server | None = None
        self._running = False
        self._last: LastResult = LastResult()

    def update_last_result(self, result: Any, *, diff_score: float) -> None:
        """Called by the inference loop after each cascade.run().

        `result` is a CascadeResult (from src/jchick/cascade.py). We pull
        the chosen result (detail if fired, else gate) and stash the
        fields the overlay needs.
        """
        chosen = result.detail or result.gate
        self._last = LastResult(
            chickens=chosen.chickens,
            other_animals=list(chosen.other_animals),
            movement=chosen.movement,
            confidence=chosen.confidence,
            notes=chosen.notes,
            model=chosen.model,
            latency_ms=chosen.latency_ms,
            fired=result.fired,
            diff_score=diff_score,
            frames_seen=0,  # filled by app via update_counters
            frames_inferenced=0,
            frames_fired=0,
            updated_at=time.monotonic(),
        )

    def update_counters(self, *, seen: int, inferenced: int, fired: int) -> None:
        """Called by inference loop to keep frame counters current on the HUD."""
        self._last.frames_seen = seen
        self._last.frames_inferenced = inferenced
        self._last.frames_fired = fired

    async def start(self) -> None:
        """Start the HTTP server on configured port."""
        loop = asyncio.get_running_loop()
        self._server = await loop.create_server(
            self._handler,
            host="0.0.0.0",
            port=self._port,
        )
        self._running = True
        log.info("MJPEG server listening on port %d", self._port)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        log.info("MJPEG server stopped")

    async def _handler(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle individual HTTP connection."""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=30.0)
            if not request_line:
                writer.close()
                return

            request = request_line.decode().strip().split(" ")
            if len(request) < 2:
                writer.close()
                return

            method, path = request[0], request[1]
            if method != "GET":
                writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                writer.close()
                return

            # Drain remaining headers
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=30.0)
                if line == b"\r\n" or line == b"":
                    break

            if path == "/stream":
                await self._serve_stream(writer)
            elif path == "/":
                await self._serve_viewer(writer)
            else:
                writer.write(
                    b"HTTP/1.1 404 Not Found\r\n"
                    b"Content-Type: text/plain\r\n\r\nNot Found"
                )
                writer.close()

        except Exception as e:
            log.error("stream handler error: %s", e)
            try:
                writer.close()
            except Exception:
                pass

    async def _serve_stream(self, writer: asyncio.StreamWriter) -> None:
        """Serve continuous MJPEG stream with HUD overlay."""
        head = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
            b"Connection: keep-alive\r\n"
            b"Cache-Control: no-cache\r\n"
            b"\r\n"
        )
        writer.write(head)
        await writer.drain()

        async for frame_bytes in self._capture_frames():
            if not self._running:
                break

            frame_head = (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame_bytes)).encode() + b"\r\n"
                b"\r\n"
            )
            writer.write(frame_head)
            writer.write(frame_bytes)
            await writer.drain()

            writer.write(b"\r\n")
            await writer.drain()

        log.info("Stream ended")

    async def _serve_viewer(self, writer: asyncio.StreamWriter) -> None:
        """Serve HTML viewer page."""
        html = self._viewer_html()
        head = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(head + html.encode("utf-8"))
        await writer.drain()
        log.info("Viewer page served")

    async def _capture_frames(self) -> AsyncIterator[bytes]:
        """Yield JPEG frames from capture source at desired FPS, with overlay."""
        period = 1.0 / max(self._cfg.capture_fps, 0.01)
        async for jpeg, _ts in self._source.frames():
            yield self._overlay(jpeg)
            await asyncio.sleep(period)

    def _overlay(self, jpeg: bytes) -> bytes:
        """Decode JPEG, draw HUD, re-encode. Returns new JPEG bytes.

        If decode fails (corrupt frame), returns the original bytes —
        better to show a raw frame than drop it.
        """
        try:
            img = Image.open(BytesIO(jpeg))
            img = img.convert("RGB")
        except Exception as e:
            log.debug("overlay: JPEG decode failed (%s); passing through", e)
            return jpeg

        draw = ImageDraw.Draw(img, "RGBA")
        r = self._last
        age = time.monotonic() - r.updated_at if r.updated_at else 0.0

        # Color: green if chickens>0 and confident, yellow if low/conf,
        # red if stale (>10s), gray if no result yet.
        if r.updated_at == 0.0:
            bar = (90, 90, 90, 200)
        elif age > 10.0:
            bar = (180, 60, 60, 220)
        elif r.chickens > 0:
            bar = (60, 180, 90, 220)
        else:
            bar = (200, 180, 60, 200)

        # Top status bar
        lines = []
        if r.updated_at == 0.0:
            lines.append("waiting for first inference...")
        else:
            lines.append(
                f"chickens={r.chickens}  conf={r.confidence*100:.0f}%  "
                f"move={r.movement}  {'FIRED' if r.fired else 'gated'}"
            )
            if r.other_animals:
                lines.append("other: " + ", ".join(r.other_animals))
            lines.append(
                f"diff={r.diff_score:.4f}  {r.model}  {r.latency_ms}ms  "
                f"age={age:.1f}s"
            )
        lines.append(
            f"seen={r.frames_seen} inf={r.frames_inferenced} fired={r.frames_fired}  "
            f"{self._cfg.capture_source}@{self._cfg.capture_fps:.2ffps}"
        )

        # Draw semi-transparent bar across the top, then text on top.
        bar_h = 18 * len(lines) + 8
        draw.rectangle([0, 0, img.width, bar_h], fill=bar)
        y = 4
        for line in lines:
            draw.text((6, y), line, fill=(255, 255, 255, 255))
            y += 18

        # Notes (if any) in a smaller bottom bar
        if r.notes:
            nb_h = 22
            draw.rectangle(
                [0, img.height - nb_h, img.width, img.height],
                fill=(0, 0, 0, 160),
            )
            draw.text((6, img.height - nb_h + 3), r.notes[:120], fill=(255, 255, 255, 255))

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def _viewer_html(self) -> str:
        """Return HTML viewer page."""
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jetson Picchk Camera</title>
    <style>
        body {
            margin: 0;
            padding: 20px;
            background: #1a1a1a;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        .container {
            max-width: 1280px;
            margin: 0 auto;
        }
        h1 {
            margin-bottom: 10px;
            font-size: 24px;
        }
        .status {
            font-size: 14px;
            color: #888;
            margin-bottom: 20px;
        }
        .camera-wrapper {
            position: relative;
            width: 100%;
            max-width: 1280px;
            margin: 0 auto;
        }
        #camera {
            width: 100%;
            height: auto;
            display: block;
            border-radius: 8px;
        }
        .placeholder {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: #666;
            font-size: 18px;
        }
        .loading {
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(0,0,0,0.7);
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Jetson Picchk Camera</h1>
        <div class="status">Capturing at {capture_fps} fps | Source: {capture_source}</div>
        <div class="camera-wrapper">
            <img id="camera" src="/stream" alt="Camera stream" style="display:none;">
            <div class="placeholder" id="placeholder">Connecting to camera...</div>
            <div class="loading">Refresh page to reconnect</div>
        </div>
    </div>
    <script>
        const img = document.getElementById('camera');
        const placeholder = document.getElementById('placeholder');

        img.onload = function() {
            this.style.display = 'block';
            placeholder.style.display = 'none';
        };

        img.onerror = function() {
            placeholder.textContent = 'Camera connection lost. Refreshing...';
            setTimeout(() => location.reload(), 2000);
        };
    </script>
</body>
</html>""".format(
            capture_fps=self._cfg.capture_fps,
            capture_source=self._cfg.capture_source,
        )
```

## Validation plan

    python3 -m py_compile src/jchick/mjpeg_server.py
    # Expected: no output, exit 0

    python3 -c "from jchick.mjpeg_server import MJPEGServer, LastResult; print('ok')"
    # Expected: ok   (run from repo root with src on path; if venv active
    #                   and package not installed, may need PYTHONPATH=src)

## Success criteria
- [ ] `py_compile` passes
- [ ] `MJPEGServer` and `LastResult` import cleanly
- [ ] No references to `cfg.` (should all be `self._cfg.`)
- [ ] No external deps beyond PIL + stdlib

## Rollback
    rm src/jchick/mjpeg_server.py

## Next task
After confirmed complete: execute T02