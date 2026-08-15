"""Simple MJPEG streaming server with a separate HUD text panel.

Provides:
- /stream endpoint serving multipart/x-mixed-replace video stream
  (clean frames — no on-image overlay)
- /state endpoint returning current inference HUD as JSON (polled by
  the viewer page and rendered as HTML text below the image)
- /trigger endpoint (POST) arming a one-shot manual trigger that
  bypasses the motion diff gate on the next frame
- / endpoint serving a simple HTML viewer page with the camera image,
  an HUD text panel, and a 'Trigger now' button
- LastResult shared state: inference loop writes, /state reads
- The inference loop checks consume_trigger() each frame to decide
  whether to bypass the diff gate.

Uses Python stdlib only — no external dependencies.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

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
        self._last_jpeg: bytes | None = None
        self._trigger = False

    def update_last_frame(self, jpeg: bytes) -> None:
        """Called by the inference loop on every captured frame (before diff gate).

        The stream loop reads self._last_jpeg. This decouples the stream
        from the single-consumer async generator on self._source.frames()
        — only the inference loop iterates that generator; the MJPEG
        server re-serves whatever the loop most recently saw.
        """
        self._last_jpeg = jpeg

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

    def request_trigger(self) -> bool:
        """Called by HTTP /trigger handler. Returns True if armed."""
        self._trigger = True
        return True

    def consume_trigger(self) -> bool:
        """Called by inference loop each frame. Returns True once, then resets."""
        if self._trigger:
            self._trigger = False
            return True
        return False

    async def start(self) -> None:
        """Start the HTTP server on configured port."""
        self._server = await asyncio.start_server(
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
            if method not in ("GET", "POST"):
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
            elif path == "/state":
                await self._serve_state(writer)
            elif path == "/trigger":
                await self._serve_trigger(writer, method)
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

    async def _serve_state(self, writer: asyncio.StreamWriter) -> None:
        """Serve current HUD state as JSON for the viewer's text panel."""
        import json

        r = self._last
        age = time.monotonic() - r.updated_at if r.updated_at else 0.0
        payload = {
            "chickens": r.chickens,
            "other_animals": r.other_animals,
            "movement": r.movement,
            "confidence": r.confidence,
            "notes": r.notes,
            "model": r.model,
            "latency_ms": r.latency_ms,
            "fired": r.fired,
            "diff_score": r.diff_score,
            "frames_seen": r.frames_seen,
            "frames_inferenced": r.frames_inferenced,
            "frames_fired": r.frames_fired,
            "age_s": round(age, 1),
            "has_result": r.updated_at != 0.0,
            "fps": self._cfg.capture_fps,
            "source": self._cfg.capture_source,
        }
        body = json.dumps(payload).encode("utf-8")
        head = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"\r\n"
        )
        writer.write(head + body)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    async def _serve_trigger(self, writer: asyncio.StreamWriter, method: str) -> None:
        """Arm a manual trigger that bypasses the diff gate on the next frame."""
        if method != "POST":
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            writer.close()
            return
        self.request_trigger()
        body = b'{"ok":true}'
        head = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"\r\n"
        )
        writer.write(head + body)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        log.info("manual trigger armed")

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
        html = self._viewer_html().encode("utf-8")
        head = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"Content-Length: " + str(len(html)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(head + html)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        log.info("Viewer page served")

    async def _capture_frames(self) -> AsyncIterator[bytes]:
        """Yield the most recent JPEG at the stream's own pace, with overlay.

        We do NOT iterate self._source.frames() here — that async generator
        has a single consumer (the inference loop in app.py). Instead we
        read self._last_jpeg, which the inference loop updates on every
        captured frame via update_last_frame(). If no frame has arrived
        yet, we sleep and retry.
        """
        period = 1.0 / max(self._cfg.capture_fps, 0.01)
        while self._running:
            jpeg = self._last_jpeg
            if jpeg is not None:
                yield jpeg
            await asyncio.sleep(period)

    def _viewer_html(self) -> str:
        """Return HTML viewer page.

        Layout: clean camera image on top, an HUD text panel below it
        (fed by polling /state), and a 'Trigger now' button that POSTs
        to /trigger to force inference on the next frame regardless of
        motion.
        """
        # NOTE: do NOT use str.format() here — the CSS contains literal
        # { } braces that .format() would try to interpret as field names.
        # Plain .replace() avoids the footgun.
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jetson PiChick Camera</title>
    <style>
        body {
            margin: 0;
            padding: 20px;
            background: #1a1a1a;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        .container { max-width: 1280px; margin: 0 auto; }
        .header-row { display: flex; align-items: center; gap: 16px; margin-bottom: 10px; }
        h1 { margin: 0; font-size: 24px; }
        .status { font-size: 14px; color: #888; margin-bottom: 20px; }
        .camera-wrapper { position: relative; width: 100%; max-width: 1280px; margin: 0 auto; }
        #camera { width: 100%; height: auto; display: block; border-radius: 8px; }
        .placeholder {
            position: absolute; top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            color: #666; font-size: 18px;
        }
        .loading {
            position: absolute; top: 10px; right: 10px;
            background: rgba(0,0,0,0.7); padding: 8px 12px;
            border-radius: 4px; font-size: 12px;
        }
        .hud {
            margin: 16px auto 0; max-width: 1280px;
            background: #222; border: 1px solid #333;
            border-radius: 8px; padding: 14px 16px;
            font-family: "SF Mono", Menlo, Consolas, monospace;
            font-size: 13px; line-height: 1.5;
            min-height: 80px;
        }
        .hud-row { display: flex; gap: 10px; flex-wrap: wrap; }
        .hud-field { color: #aaa; }
        .hud-field b { color: #e0e0e0; font-weight: 600; }
        .hud-notes { margin-top: 8px; color: #9ac; font-style: italic; }
        .hud-empty { color: #666; }
        .fired-yes { color: #6c6; font-weight: 700; }
        .fired-no  { color: #cc6; }
        #trigger-btn {
            margin-left: auto;
            padding: 8px 16px; font-size: 14px;
            background: #2a6; color: #fff; border: none;
            border-radius: 6px; cursor: pointer;
            transition: background 0.15s;
        }
        #trigger-btn:hover { background: #3b7; }
        #trigger-btn:disabled { background: #555; cursor: wait; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header-row">
            <h1>Jetson PiChick Camera</h1>
            <button id="trigger-btn" type="button">Trigger now</button>
        </div>
        <div class="status">Capturing at __FPS__ fps | Source: __SOURCE__</div>
        <div class="camera-wrapper">
            <img id="camera" src="/stream" alt="Camera stream" style="display:none;">
            <div class="placeholder" id="placeholder">Connecting to camera...</div>
            <div class="loading">Refresh page to reconnect</div>
        </div>
        <div class="hud" id="hud">
            <div class="hud-empty">waiting for first inference...</div>
        </div>
    </div>
    <script>
        const img = document.getElementById('camera');
        const placeholder = document.getElementById('placeholder');
        const hud = document.getElementById('hud');
        const triggerBtn = document.getElementById('trigger-btn');

        img.onload = function() { this.style.display='block'; placeholder.style.display='none'; };
        img.onerror = function() {
            placeholder.textContent = 'Camera connection lost. Refreshing...';
            setTimeout(() => location.reload(), 2000);
        };

        function renderHud(s) {
            if (!s.has_result) {
                hud.innerHTML = '<div class="hud-empty">waiting for first inference...</div>';
                return;
            }
            const fired = s.fired
                ? '<span class="fired-yes">FIRED</span>'
                : '<span class="fired-no">gated</span>';
            const others = s.other_animals && s.other_animals.length
                ? s.other_animals.join(', ') : 'none';
            let html = '<div class="hud-row">'
                + '<span class="hud-field">chickens <b>' + s.chickens + '</b></span>'
                + '<span class="hud-field">conf <b>' + (s.confidence*100).toFixed(0) + '%</b></span>'
                + '<span class="hud-field">move <b>' + s.movement + '</b></span>'
                + '<span class="hud-field">state <b>' + fired + '</b></span>'
                + '<span class="hud-field">other <b>' + others + '</b></span>'
                + '</div>'
                + '<div class="hud-row">'
                + '<span class="hud-field">diff <b>' + s.diff_score.toFixed(4) + '</b></span>'
                + '<span class="hud-field">model <b>' + s.model + '</b></span>'
                + '<span class="hud-field">lat <b>' + s.latency_ms + 'ms</b></span>'
                + '<span class="hud-field">age <b>' + s.age_s + 's</b></span>'
                + '</div>'
                + '<div class="hud-row">'
                + '<span class="hud-field">seen <b>' + s.frames_seen + '</b></span>'
                + '<span class="hud-field">inf <b>' + s.frames_inferenced + '</b></span>'
                + '<span class="hud-field">fired <b>' + s.frames_fired + '</b></span>'
                + '<span class="hud-field">cap <b>' + s.source + '@' + s.fps + 'fps</b></span>'
                + '</div>';
            if (s.notes) html += '<div class="hud-notes">' + s.notes + '</div>';
            hud.innerHTML = html;
        }

        async function pollState() {
            try {
                const r = await fetch('/state', {cache: 'no-store'});
                if (r.ok) renderHud(await r.json());
            } catch (e) { /* ignore transient errors */ }
        }
        setInterval(pollState, 1000);
        pollState();

        triggerBtn.addEventListener('click', async () => {
            triggerBtn.disabled = true;
            try {
                await fetch('/trigger', {method: 'POST'});
            } catch (e) { /* ignore */ }
            setTimeout(() => { triggerBtn.disabled = false; }, 3000);
        });
    </script>
</body>
</html>""".replace("__FPS__", str(self._cfg.capture_fps)).replace(
            "__SOURCE__", self._cfg.capture_source
        )