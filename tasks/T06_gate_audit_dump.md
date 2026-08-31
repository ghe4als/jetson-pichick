# T06 — Gate-frame audit dump

## Goal
Capture every frame that reached the gate model but where the gate reported no animals (the `inference.gated` case), as JPEG + JSON sidecar on disk. These frames become the fixed A/B test set for T07's eval harness: the same images the model dismissed, replayable against any prompt or model offline.

## Context
The investigation (`tasks/diff-gate-investigation-2026-08-28.md`) concluded the pixel diff gate is healthy (scores 0.012-0.036 during daylight = real motion passing) but the gate VLM (`llava-phi3:3.8b`) returns `chickens: 0, movement: still` on every frame. journalctl gives us the model's verdicts but NOT the frame pixels. Without the images we cannot tell whether the fault is the prompt/model or the camera framing/lighting, and we cannot A/B test prompt changes against a fixed set of frames. This task adds an opt-in dump of exactly those disputed frames. Shipped DISABLED by default; T08 enables it for the baseline capture.

Design choice: dump every `not result.fired` frame (i.e. every `inference.gated` frame). The diff gate upstream already filters hard (~2274 inferenced frames over many hours per the heartbeat), so volume is bounded. A rotating cap prevents unbounded disk growth. Each JSON sidecar carries the gate's verdict and `diff_score`, so each image is self-describing and lines up with a logged `inference.gated` line by timestamp.

## Files touched
- `src/jchick/config.py` — add `gate_audit_dir: str` field + `JCHICK_GATE_AUDIT_DIR` env read (default `""` = disabled).
- `.env.example` — document `JCHICK_GATE_AUDIT_DIR` (shipped empty/disabled).
- `src/jchick/audit.py` — NEW. `GateAudit` class: `record(jpeg, diff_score, gate)` writes `<utc_ms>_<score>.jpg` + `.json` sidecar; rotating cap deletes oldest when count exceeds `max_files` (default 500).
- `src/jchick/app.py` — construct `GateAudit` in `App.__init__` when configured; call `record()` in `_inference_loop` for every `not result.fired` frame.

## Pre-conditions
- [ ] T05 must be complete (it is — service deployed and streaming).

## Exact changes required

### Change 1: add config field
File: `src/jchick/config.py`
Action: INSERT a new field and wire it in `from_env`.

In the `Config` dataclass body, after the `http_port: int` field, add:
```
    # diagnostics
    gate_audit_dir: str       # "" = disabled; dir to dump gate-dismissed frames
```

In `Config.from_env`, inside the `return cls(...)` call, after the `http_port=...` line, add:
```
            gate_audit_dir=e.get("JCHICK_GATE_AUDIT_DIR", ""),
```

### Change 2: document env var (disabled by default)
File: `.env.example`
Action: INSERT a new block after the web-viewer block (after the `JCHICK_HTTP_PORT=8090` line) and before the `# Logging` block:
```
# Gate-frame audit: when set to a directory, dump every frame that reached the
# gate model but where the gate reported no animals (the "model says empty,
# diff said motion" case). Writes <ts>_<diffscore>.jpg + .json sidecar, rotated
# to a 500-frame cap. Empty = disabled. The dumped frames are the A/B test set
# for scripts/eval_gate.py (see tasks/diff-gate-investigation-2026-08-28.md).
JCHICK_GATE_AUDIT_DIR=
```

### Change 3: new GateAudit module
File: `src/jchick/audit.py`
Action: CREATE. Full content:
```python
"""Gate-frame audit dump.

When enabled (JCHICK_GATE_AUDIT_DIR set), writes every frame the gate model
dismissed (no animals reported) to disk as JPEG + JSON sidecar, so the
"model says empty, diff said motion" case can be replayed offline against
any prompt or model by scripts/eval_gate.py.

Rotating cap: once the directory exceeds ``max_files`` JPEGs, the oldest are
deleted. Bounded so a long-running service can't fill the disk.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .ollama import VisionResult

log = logging.getLogger(__name__)

_MAX_FILES = 500


class GateAudit:
    def __init__(self, dir_path: str, *, max_files: int = _MAX_FILES) -> None:
        self._dir = Path(dir_path)
        self._max_files = max(1, max_files)
        self._dir.mkdir(parents=True, exist_ok=True)
        log.info("gate audit: dumping dismissed frames to %s (cap=%d)",
                 self._dir, self._max_files)

    def record(self, jpeg: bytes, *, diff_score: float, gate: VisionResult) -> None:
        ts_ms = int(time.time() * 1000)
        stem = f"{ts_ms}_{diff_score:.4f}"
        jpg_path = self._dir / f"{stem}.jpg"
        json_path = self._dir / f"{stem}.json"
        try:
            jpg_path.write_bytes(jpeg)
            json_path.write_text(json.dumps({
                "ts": ts_ms,
                "diff_score": round(diff_score, 4),
                "model": gate.model,
                "chickens": gate.chickens,
                "other_animals": gate.other_animals,
                "movement": gate.movement,
                "confidence": gate.confidence,
                "notes": gate.notes,
                "latency_ms": gate.latency_ms,
            }))
        except OSError as e:
            log.debug("gate audit write failed: %s", e)
            return
        self._rotate()

    def _rotate(self) -> None:
        try:
            jpgs = sorted(self._dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        excess = len(jpgs) - self._max_files
        for p in jpgs[:max(0, excess)]:
            sidecar = p.with_suffix(".json")
            try:
                p.unlink()
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                pass
```

### Change 4: wire GateAudit into the app
File: `src/jchick/app.py`
Action: import + construct + call.

Add to the imports near the other `from .` imports:
```python
from .audit import GateAudit
```

In `App.__init__`, after the `self._ip = _local_ip(cfg.nats_url)` line, add:
```python
        self._audit: GateAudit | None = None
        if cfg.gate_audit_dir:
            self._audit = GateAudit(cfg.gate_audit_dir)
```

In `_inference_loop`, after the `await self._publish_inference(result, diff_score=score)` line (the last statement inside the `async for` body, before the `except asyncio.CancelledError`), add:
```python
                if self._audit is not None and not result.fired:
                    self._audit.record(jpeg, diff_score=score, gate=result.gate)
```

## Validation plan
Run from the repo root on the dev box:

```
python3 -m py_compile src/jchick/audit.py src/jchick/config.py src/jchick/app.py
# Expected: no output, exit 0

python3 -c "from jchick.config import Config; c=Config.from_env({'NATS_URL':'nats://x','OLLAMA_URL':'http://x','JCHICK_GATE_AUDIT_DIR':'/tmp/jchick_audit_test'}); print(c.gate_audit_dir)"
# Expected: /tmp/jchick_audit_test

python3 - <<'PY'
import io
from PIL import Image
from pathlib import Path
from jchick.audit import GateAudit
from jchick.ollama import VisionResult
d = Path('/tmp/jchick_audit_test'); d.mkdir(parents=True, exist_ok=True)
a = GateAudit(str(d), max_files=3)
buf = io.BytesIO(); Image.new('RGB',(8,8),(10,20,30)).save(buf, 'JPEG'); jpg = buf.getvalue()
vr = VisionResult(chickens=0, other_animals=[], movement='still', confidence=0.9,
                  notes='none', model='test', latency_ms=10, raw={})
for i in range(5): a.record(jpg, diff_score=0.02, gate=vr)
print('jpgs after 5 writes with cap=3:', len(list(d.glob('*.jpg'))))
print('jsons after 5 writes with cap=3:', len(list(d.glob('*.json'))))
# Expected: jpgs=3, jsons=3  (rotation deleted 2 oldest pairs)
PY
# Expected:
# jpgs after 5 writes with cap=3: 3
# jsons after 5 writes with cap=3: 3

rm -rf /tmp/jchick_audit_test
# Expected: (no output)
```

## Success criteria
- [ ] All validation commands return expected output.
- [ ] `git diff` shows only the 4 files listed (config.py, .env.example, audit.py new, app.py).
- [ ] `.env.example` ships `JCHICK_GATE_AUDIT_DIR=` (empty = disabled by default).

## Rollback
```
git checkout src/jchick/config.py src/jchick/app.py .env.example
rm src/jchick/audit.py
```

## Next task
After confirmed complete: execute T07 (offline gate eval harness). T06 produces the test set; T07 is the tool that consumes it.