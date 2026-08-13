# T03 — Wire MJPEG server into src/jchick/app.py

## Goal
Wire the MJPEG server into the App class: instantiate when
`cfg.http_port > 0`, start as a background task, feed detection results
into it from `_inference_loop`, stop on shutdown. Also fix the
`cfg.http_port` → `self._cfg.http_port` bug that was in the stale
`jchick/app.py:75`.

## Context
`src/jchick/app.py` currently has no MJPEG wiring at all (the stale
`jchick/app.py` does, but with the bug). We add:

- import MJPEGServer
- `self._mjpeg` field, instantiated when `cfg.http_port > 0`
- start task in `run()`, log the port
- call `self._mjpeg.update_last_result(result, diff_score=score)` after
  each successful cascade.run() in `_inference_loop`
- call `self._mjpeg.update_counters(...)` after each inference
- stop in the `finally` block of `run()`

## Files touched
- `src/jchick/app.py` — add import, field, start/stop, update calls

## Pre-conditions
- [ ] T01 must be complete
- [ ] T02 must be complete

## Exact changes required

### Change 1: Add import

File: `src/jchick/app.py`
Action: INSERT one import after the `NatsPublisher` import (line 31)

Before:
```
from .nats_pub import NatsPublisher
from .ollama import OllamaClient, OllamaError
```

After:
```
from .mjpeg_server import MJPEGServer
from .nats_pub import NatsPublisher
from .ollama import OllamaClient, OllamaError
```

### Change 2: Add self._mjpeg field in __init__

File: `src/jchick/app.py`
Action: INSERT after `self._pub = NatsPublisher(cfg.nats_url)` (line 53)

Before:
```
        self._pub = NatsPublisher(cfg.nats_url)
        self._last_chickens: int = 0
```

After:
```
        self._pub = NatsPublisher(cfg.nats_url)
        self._mjpeg: MJPEGServer | None = None
        if cfg.http_port > 0:
            self._mjpeg = MJPEGServer(cfg, self._source)
        self._last_chickens: int = 0
```

### Change 3: Start MJPEG task in run()

File: `src/jchick/app.py`
Action: INSERT between the `asyncio.create_task(self._pub.start(), ...)`
line and `await self._publish_startup()` (lines 67-68)

Before:
```
        asyncio.create_task(self._pub.start(), name="nats_connect")
        await self._publish_startup()
```

After:
```
        asyncio.create_task(self._pub.start(), name="nats_connect")
        mjpeg_task = None
        if self._mjpeg is not None:
            mjpeg_task = asyncio.create_task(self._mjpeg.start(), name="mjpeg")
            log.info("MJPEG streaming enabled on port %d", self._cfg.http_port)
        await self._publish_startup()
```

### Change 4: Stop MJPEG in finally block

File: `src/jchick/app.py`
Action: INSERT before `await self._pub.stop()` (line 81)

Before:
```
            await self._publish_shutdown()
            await self._pub.stop()
```

After:
```
            await self._publish_shutdown()
            if self._mjpeg:
                await self._mjpeg.stop()
            await self._pub.stop()
```

### Change 5: Feed results + counters to MJPEG in _inference_loop

File: `src/jchick/app.py`
Action: INSERT after the `if result.fired:` block and before
`await self._publish_inference(...)` (lines 105-107)

Before:
```
                if result.fired:
                    self._frames_fired += 1
                await self._publish_inference(result, diff_score=score)
```

After:
```
                if result.fired:
                    self._frames_fired += 1
                if self._mjpeg is not None:
                    self._mjpeg.update_last_result(result, diff_score=score)
                    self._mjpeg.update_counters(
                        seen=self._frames_seen,
                        inferenced=self._frames_inferenced,
                        fired=self._frames_fired,
                    )
                await self._publish_inference(result, diff_score=score)
```

## Validation plan

    python3 -m py_compile src/jchick/app.py
    # Expected: no output, exit 0

    python3 -c "from jchick.app import App; print('ok')"
    # Expected: ok

    # Full smoke: import resolves, no NameError on construction with http_port=0
    python3 -c "
    import os
    from jchick.config import Config
    from jchick.app import App
    os.environ['NATS_URL'] = 'nats://x:4222'
    os.environ['OLLAMA_URL'] = 'http://x:11434'
    c = Config.from_env()
    a = App(c)
    assert a._mjpeg is None  # http_port defaults to 0
    print('ok http_port=0 disables mjpeg')
    "
    # Expected: ok http_port=0 disables mjpeg

    # And with http_port set:
    python3 -c "
    import os
    from jchick.config import Config
    from jchick.app import App
    os.environ['NATS_URL'] = 'nats://x:4222'
    os.environ['OLLAMA_URL'] = 'http://x:11434'
    os.environ['JCHICK_HTTP_PORT'] = '8090'
    c = Config.from_env()
    a = App(c)
    assert a._mjpeg is not None
    print('ok http_port=8090 enables mjpeg')
    "
    # Expected: ok http_port=8090 enables mjpeg

## Success criteria
- [ ] `py_compile` passes
- [ ] App constructs with http_port=0 (mjpeg None) and http_port=8090 (mjpeg set)
- [ ] No `cfg.` reference where `self._cfg` is meant (the bug from stale tree is gone)
- [ ] Inference loop calls update_last_result + update_counters when mjpeg is set

## Rollback
    git checkout src/jchick/app.py

## Next task
After confirmed complete: execute T04