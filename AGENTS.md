# jetson-pichick — Agent Guide

Standalone chicken-coop camera node for the NVIDIA Jetson Orin Nano.
Research spike: minimum-viable code, hard to break, easy to read. This
is the PRODUCER half of the coop pipeline (consumer:
`../coop_door_controller`, ESP32 door controller).

## Two-repo wire contract (read before touching NATS/payload code)
- This repo publishes `home.coop.pichick.inference.fired` with payload:
  `{host, ip, ts, chickens, diff_score, gate:{...}, detail:{...}|null}`
  — the top-level `chickens` is the chosen count (detail if available,
  else gate) and must serialize FIRST (see `_publish_inference` in
  `src/jchick/app.py`). The ESP32 consumer reads the top-level field
  only, but do not regress key order.
- Any claim about the inference payload must cite BOTH this repo's
  publish line (`src/jchick/app.py`, `src/jchick/nats_pub.py`) AND the
  consumer parse line (`coop_door_controller/src/nats_integration.c`).
  Never declare the pipeline fixed by inspecting one side.
- Docs (PLAN, RESUME, handoffs, task files) are claims. Source code and
  actual tool results are authoritative.

## Deploy (user-gated)
Do not rsync, install, or restart the service on the Jetson unless the
user approves it in the current session. When approved:

    rsync -av --exclude .venv --exclude __pycache__ \
      ~/code/jetson-pichick/ pichick@192.168.0.18:/tmp/jetson-pichick/
    ssh pichick@192.168.0.18
    sudo bash /tmp/jetson-pichick/scripts/install.sh
    sudo systemctl restart jetson-pichick
    journalctl -u jetson-pichick -f          # logs
    nats sub 'home.coop.>'                    # verify subjects arrive

## Config source of truth
All runtime config lives in `.env.example` in this repo. `install.sh`
re-seeds `/etc/jchick/jchick.env` from it on every run (with a .bak
backup). NEVER hand-edit `/etc/jchick/jchick.env` on the box — config
changes ship with a normal deploy.

## Build/verify (dev box, no hardware)
    python3 -m py_compile src/jchick/app.py src/jchick/nats_pub.py   # syntax
    nats sub 'home.coop.>'                                          # runtime proof

Runtime claims ("the Jetson is publishing X") require an actual
`nats sub` / `journalctl` tool result from this session — never infer
them. The gate model returning `chickens:0` on every frame is a model
issue, not a code issue; prove any fix with a live `inference.fired`
message before declaring it resolved.

## Models
Picked at startup from `JCHICK_GATE_MODEL` / `JCHICK_DETAIL_MODEL` (see
`.env.example`); swap and `sudo systemctl restart jetson-pichick`. The
Orin Nano dev kit shares 7.5 GB between CPU and GPU — stay under ~5 GB
per loaded model or the kernel starts swapping. `ollama ps` shows what
is actually loaded.

## NATS subjects
All published under `home.coop.<host>.*`: `status.startup`,
`status.shutdown`, `status.heartbeat`, `status.tegra`,
`inference.gated`, `inference.fired`, `detection.chicken`,
`detection.<species>`, `alert.ollama`.