# T02 — Add http_port to src/jchick/config.py + JCHICK_HTTP_PORT to .env.example

## Goal
Add `http_port: int` field to the packaged `Config` dataclass and read
`JCHICK_HTTP_PORT` from env, default 0 (disabled). Add the var to
`.env.example` with a sensible default of 8090 so the stream is on by
default once T03 wires it in.

## Context
`jchick/config.py` (stale tree) already has this field at line 46 and
reads it at line 72. We port those two additions to `src/jchick/config.py`.
`.env.example` has no `JCHICK_HTTP_PORT` line — add one near the
logging section at the end.

## Files touched
- `src/jchick/config.py` — add field + env read
- `.env.example` — add JCHICK_HTTP_PORT=8090

## Pre-conditions
- [ ] T01 must be complete

## Exact changes required

### Change 1: Add http_port field to Config dataclass

File: `src/jchick/config.py`
Action: INSERT one field after `tegrastats_seconds: float` (line 44)

Before:
```
    # publishing
    host: str                 # used in subject prefix home.coop.<host>.*
    heartbeat_seconds: float
    tegrastats_seconds: float

    @classmethod
```

After:
```
    # publishing
    host: str                 # used in subject prefix home.coop.<host>.*
    heartbeat_seconds: float
    tegrastats_seconds: float

    # web viewer
    http_port: int            # 0 = disabled; 8090 = default stream port

    @classmethod
```

### Change 2: Read JCHICK_HTTP_PORT from env in from_env()

File: `src/jchick/config.py`
Action: INSERT one line after `tegrastats_seconds=float(...)` (line 69)

Before:
```
            heartbeat_seconds=float(e.get("JCHICK_HEARTBEAT_SECONDS", "300")),
            tegrastats_seconds=float(e.get("JCHICK_TEGRASTATS_SECONDS", "60")),
        )
```

After:
```
            heartbeat_seconds=float(e.get("JCHICK_HEARTBEAT_SECONDS", "300")),
            tegrastats_seconds=float(e.get("JCHICK_TEGRASTATS_SECONDS", "60")),
            http_port=int(e.get("JCHICK_HTTP_PORT", "0")),
        )
```

### Change 3: Add JCHICK_HTTP_PORT to .env.example

File: `.env.example`
Action: INSERT before the `# Logging` section

Before:
```
# Logging
JCHICK_LOG_LEVEL=INFO
```

After:
```
# Web viewer (MJPEG stream with detection overlay).
# 0 = disabled. 8090 = stream at http://<jetson>:8090/
JCHICK_HTTP_PORT=8090

# Logging
JCHICK_LOG_LEVEL=INFO
```

## Validation plan

    python3 -m py_compile src/jchick/config.py
    # Expected: no output, exit 0

    python3 -c "
    import os
    from jchick.config import Config
    os.environ['NATS_URL'] = 'nats://x:4222'
    os.environ['OLLAMA_URL'] = 'http://x:11434'
    os.environ['JCHICK_HTTP_PORT'] = '8090'
    c = Config.from_env()
    assert c.http_port == 8090, c.http_port
    del os.environ['JCHICK_HTTP_PORT']
    c2 = Config.from_env()
    assert c2.http_port == 0, c2.http_port
    print('ok')
    "
    # Expected: ok

## Success criteria
- [ ] `py_compile` passes
- [ ] Config round-trips the new field
- [ ] Default is 0 when env var absent
- [ ] `.env.example` has the new line

## Rollback
    git checkout src/jchick/config.py .env.example

## Next task
After confirmed complete: execute T03