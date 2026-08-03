#!/usr/bin/env bash
# Live MJPEG camera viewer for aiming/focusing. Runs independently of jchick.
#
# Starts an HTTP server on port ${PORT:-8090} serving /cam.mjpg from
# /dev/video0. View in any browser at http://<jetson>:8090/cam.mjpg
#
# Usage:
#   bash scripts/cam-view.sh                 # default 1280x720@30, port 8090
#   PORT=9000 WIDTH=1920 HEIGHT=1080 bash scripts/cam-view.sh
#
# Stop: Ctrl-C, or  pkill -f cam-view
#
# Requires: ffmpeg, python3 (both already on the Jetson via install.sh).
set -euo pipefail

PORT="${PORT:-8090}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"
DEVICE="${DEVICE:-/dev/video0}"

exec python3 - "$PORT" "$WIDTH" "$HEIGHT" "$FPS" "$DEVICE" <<'PYEOF'
import http.server, socketserver, subprocess, sys

PORT, WIDTH, HEIGHT, FPS, DEVICE = sys.argv[1:6]
BOUNDARY = "ffmpeg"

FFMPEG_CMD = [
    "ffmpeg", "-loglevel", "error",
    "-f", "v4l2", "-input_format", "mjpeg",
    "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS),
    "-i", DEVICE, "-c:v", "copy", "-f", "mjpeg", "-",
]

def find_jpegs(buf):
    """Yield (start, end_exclusive) for each JPEG in buf (FFD8..FFD9)."""
    i = 0
    while True:
        soi = buf.find(b"\xff\xd8", i)
        if soi == -1:
            return
        eoi = buf.find(b"\xff\xd9", soi + 2)
        if eoi == -1:
            # incomplete frame; keep bytes from soi onward for next pass
            yield (soi, None)
            return
        yield (soi, eoi + 2)
        i = eoi + 2

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/cam.mjpg", "/"):
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-cache, private")
        self.end_headers()
        try:
            proc = subprocess.Popen(FFMPEG_CMD, stdout=subprocess.PIPE,
                                    stderr=sys.stderr)
            leftover = b""
            while not self.wfile.closed:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf = leftover + chunk
                consumed = 0
                for soi, eoi in find_jpegs(buf):
                    if eoi is None:
                        # incomplete; save for next iteration
                        break
                    frame = buf[soi:eoi]
                    self.wfile.write(f"--{BOUNDARY}\r\n".encode())
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    consumed = eoi
                leftover = buf[consumed:] if consumed else buf
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            sys.stderr.write(f"cam-view: {e}\n")
        finally:
            try: proc.terminate()
            except Exception: pass

    def log_message(self, *a): pass

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

print(f"cam-view: serving http://0.0.0.0:{PORT}/cam.mjpg "
      f"({WIDTH}x{HEIGHT}@{FPS} from {DEVICE})", flush=True)
Server(("0.0.0.0", int(PORT)), Handler).serve_forever()
PYEOF