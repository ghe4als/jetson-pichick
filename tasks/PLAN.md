# Project Plan — MJPEG stream with detection overlay

## Goal
Port the half-built MJPEG server from the stale `jchick/` tree into the
packaged `src/jchick/` tree, add detection overlay (text-based — no
bounding boxes available from current Ollama schema), add a shared
last-result store, configure the port, deploy to the running Jetson,
and verify the stream works in a browser.

## Context
The MJPEG server was implemented in `jchick/mjpeg_server.py` (250 lines,
looks complete) plus `http_port` in `jchick/config.py` and wiring in
`jchick/app.py`. But `pyproject.toml:24` packages `src/jchick/`, not
`jchick/`. The Jetson `pip install -e /opt/jchick` only sees `src/jchick/`.
Result: the MJPEG code never ran on the device.

Confirmed on the Jetson:
```
/opt/jchick/.venv/bin/python -c "import jchick, inspect; print(inspect.getsourcefile(jchick))"
→ /opt/jchick/src/jchick/__init__.py
```
So `python -m jchick` loads `src/jchick/`, which has no mjpeg_server.py,
no http_port in config, no wiring in app.py.

Overlay design constraint: `VisionResult` (src/jchick/ollama.py:33-42)
has chickens (int), other_animals (list[str]), movement (str),
confidence (float), notes (str), model, latency_ms — NO bounding boxes.
The Ollama prompt only asks for count/species/movement. Overlay is
therefore text HUD: chicken count, confidence %, movement, gate/detail
labels, diff score, fps, timestamp, frame counter. No boxes.

Additional bug in the stale tree: `jchick/app.py:75` references
`cfg.http_port` but the local is `self._cfg` — would NameError at
startup. Must fix when porting.

## Status
Current task: T05 (DONE)
Last completed: T05 (commit: f507efb)

## Task list
- [x] T01 — Port mjpeg_server.py into src/jchick/ with overlay + last-result store   ✅ DONE
- [x] T02 — Add http_port to src/jchick/config.py + JCHICK_HTTP_PORT to .env.example   ✅ DONE
- [x] T03 — Wire MJPEG server into src/jchick/app.py (fix cfg→self._cfg bug)           ✅ DONE
- [x] T04 — Delete stale jchick/ top-level tree                                         ✅ DONE
- [x] T05 — Deploy to Jetson and verify stream in browser                               ✅ DONE

## Blocked
None.

## Notes
- T01-T03 touch at most 5 files and can be one task per the 5-file rule,
  but splitting keeps each commit reviewable and lets the build catch
  errors between steps. We'll do T01+T02+T03 as separate commits but
  they share one design session.
- Overlay uses PIL.ImageDraw on the JPEG before re-encoding for stream.
  No new deps — pillow is already in pyproject.toml.
- Shared state: a single asyncio-guarded `LastResult` dataclass in
  mjpeg_server.py, written by inference_loop, read by stream loop.
  No lock needed — single asyncio event loop, no threads.