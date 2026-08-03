"""Simple MJPEG streaming server for live camera feed.

Provides:
- /stream endpoint serving multipart/x-mixed-replace video stream
- / endpoint serving a simple HTML viewer page
- Runs alongside main inference loop without blocking

Uses Python stdlib only - no external dependencies.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from http import HTTPStatus
from io import BytesIO
from typing import Any

from PIL import Image

from .capture import FrameSource
from .config import Config

log = logging.getLogger(__name__)


class MJPEGServer:
    """Simple async MJPEG streaming server using HTTP 1.1 keepalive.

    Serves:
    - GET / - HTML viewer page
    - GET /stream - MJPEG stream (multipart/x-mixed-replace)
    """

    def __init__(self, cfg: Config, capture_source: FrameSource) -> None:
        self._cfg = cfg
        self._source = capture_source
        self._port = cfg.http_port
        self._server: asyncio.base_server.Server | None = None
        self._running = False
        self._last_frame: bytes | None = None

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
            # Read request line and headers
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
                assert line.endswith(b"\r\n")

            if path == "/stream":
                await self._serve_stream(writer)
            elif path == "/":
                await self._serve_viewer(writer)
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nNot Found")
                writer.close()

        except Exception as e:
            log.error("stream handler error: %s", e)
            try:
                writer.close()
            except:
                pass

    async def _serve_stream(self, writer: asyncio.StreamWriter) -> None:
        """Serve continuous MJPEG stream."""
        # Response headers
        head = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
            b"Connection: keep-alive\r\n"
            b"Cache-Control: no-cache\r\n"
            b"\r\n"
        )
        writer.write(head)
        await writer.drain()

        # Main streaming loop
        async for frame_bytes in self._capture_frames():
            if not self._running:
                break

            # Frame headers
            frame_head = (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame_bytes)).encode() + b"\r\n"
                b"\r\n"
            )
            writer.write(frame_head)
            writer.write(frame_bytes)
            await writer.drain()

            # Closing boundary with newline
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
        """Yield JPEG frames from capture source at desired FPS."""
        period = 1.0 / max(self._cfg.capture_fps, 0.01)
        async for jpeg, _ts in self._source.frames():
            yield jpeg
            await asyncio.sleep(period)

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
        <h1>🐔 Jetson Picchk Camera</h1>
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
