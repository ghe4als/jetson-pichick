# jetson-pichick

A standalone chicken-coop camera node for the **NVIDIA Jetson Orin Nano**.
Sister project to [`piChick`](../piChick) — same goal (watch the coop, publish
detections to NATS), totally different stack designed to actually use the
Jetson hardware.

## How this differs from piChick v1

| | piChick (Pi 3B+) | jetson-pichick (Orin Nano) |
|---|---|---|
| Trigger | Motion daemon on PIR-style frame diffs | Continuous capture, in-process diff gate |
| Inference | Remote Ollama on Mac, ~30-90s/frame | Local Ollama on Jetson, ~3-6s/frame |
| Models | One (`llava:7b`) | Two-stage cascade: `moondream:1.8b` gate -> `llava:7b` detail |
| Output | Free-text parsed by regex | Structured JSON via Ollama `format=json` |
| GPU telemetry | none | `tegrastats` parsed and published |
| Repo size | ~2,800 LOC | ~700 LOC |

This is intentionally a **research spike**: minimum-viable code, hard to
break, easy to read. piChick v1 stays in production on the Pi; this is what
runs on the new Jetson while we benchmark.

## Architecture

```
+----------------------------- Jetson Orin Nano ----------------------------+
|                                                                           |
|  USB UVC camera (/dev/video0)                                             |
|         |                                                                 |
|         v                                                                 |
|  capture loop (1 fps)  ---->  diff gate (PIL thumbnail, mean abs delta)   |
|                                       |                                   |
|                                       | kept frames                       |
|                                       v                                   |
|                              +----------------+                           |
|                              | gate model     |   (moondream:1.8b ~0.5s)  |
|                              | "anything live?"                           |
|                              +-------+--------+                           |
|                                      | yes                                |
|                                      v                                    |
|                              +----------------+                           |
|                              | detail model   |   (llava:7b ~3-6s)        |
|                              | counts/species |                           |
|                              +-------+--------+                           |
|                                      |                                    |
|  +-------------+                     v                                    |
|  | tegrastats  |  -->  NATS  publisher  ----+                              |
|  +-------------+                            |                              |
|                                             | nats://<mac>:4222            |
+---------------------------------------------+------------------------------+
                                              v
                                        Mac running nats-server
                                        nats sub 'home.coop.>'
```

## NATS subjects

All published under `home.coop.<host>.*` where `<host>` is the Jetson's
hostname (set `JCHICK_HOST=...` to override).

| Subject | When | Payload |
|---|---|---|
| `status.startup` | service start | version, models, capture source |
| `status.shutdown` | service stop | frames seen / inferenced / fired |
| `status.heartbeat` | every `JCHICK_HEARTBEAT_SECONDS` | counters + last chicken count |
| `status.tegra` | every `JCHICK_TEGRASTATS_SECONDS` | gpu%, ram_pct, cpu/gpu temps, mW |
| `inference.gated` | gate said "uninteresting" | gate result + diff score |
| `inference.fired` | gate said "interesting" | gate + detail result |
| `detection.chicken` | detail model saw chickens | count, confidence, model, notes |
| `detection.<species>` | detail model saw other animals | class, confidence, model |
| `alert.ollama` | inference call failed | error string |

## Quick start

Prereqs: a Jetson Orin Nano running JetPack 6 / Ubuntu 24.04, Ollama
already installed on it, models already pulled, and a NATS broker
reachable on the LAN.

```bash
# On your dev box: rsync the repo over to the Jetson
rsync -av --exclude .venv --exclude __pycache__ \
  ~/code/jetson-pichick/ pichick@192.168.0.18:/tmp/jetson-pichick/

# On the Jetson
ssh pichick@192.168.0.18
sudo bash /tmp/jetson-pichick/scripts/install.sh
sudo vi /etc/jchick/jchick.env        # set NATS_URL, OLLAMA_URL, etc
sudo systemctl start jetson-pichick
journalctl -u jetson-pichick -f
```

On any LAN host with the `nats` CLI:

```bash
nats sub 'home.coop.>'    # subjects start arriving within a few seconds
```

## Configuration

All env vars live in `/etc/jchick/jchick.env`. See [`.env.example`](.env.example)
for the full set with defaults. The two required ones are:

- `NATS_URL` — `nats://<host>:4222`
- `OLLAMA_URL` — `http://127.0.0.1:11434` for local-on-Jetson, or a LAN URL

The `synthetic` capture source generates colored test frames in process and
needs no camera. Use it to validate the install path before wiring a real
camera.

## Models

```bash
ollama pull moondream:1.8b    # cheap gate
ollama pull llava:7b          # detail (default)
ollama pull llava-phi3:3.8b   # alternate detail (smaller, faster)
ollama pull llava-llama3:8b   # alternate detail (newer, sharper)
```

Models are picked at startup from `JCHICK_GATE_MODEL` and
`JCHICK_DETAIL_MODEL`. Swap them and `systemctl restart jetson-pichick`.
The gate's job is "is anything alive in this frame"; pick the smallest
vision model you trust to say yes/no. The detail model's job is "how
many chickens, what else"; pick the best you can fit in RAM.

The Orin Nano dev kit shares 7.5 GB between CPU and GPU. Stay under ~5 GB
per loaded model or the kernel will start swapping under load. Don't
load both gate and detail simultaneously — Ollama already serializes
them.

## GPU enablement on Jetson Orin (important)

The official Ollama arm64 binary at `/usr/local/bin/ollama` is **not
compiled for the Orin's CUDA compute capability 8.7** and silently falls
back to CPU. You can confirm this by inspecting `journalctl -u ollama`
for a line like:

```
skipping CUDA device — compute capability not in compiled architectures
device=Orin cc=870 archs="[500 520 600 610 700 750 800 860 890 900 1200]"
```

To use the GPU, replace the stock systemd unit with the
`dustynv/ollama:r36.4-cu129-24.04` container, which is built for `sm_87`.

```bash
# pull the JetPack-aware image (~4 GB)
sudo docker pull dustynv/ollama:r36.4-cu129-24.04

# stop the stock ollama, install the container-wrapped unit
sudo systemctl stop ollama
sudo systemctl disable ollama
sudo cp /opt/jchick/assets/ollama-jetson.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-jetson

# verify the GPU is now in use
ollama ps   # PROCESSOR column should show GPU%, not 100% CPU
tegrastats --interval 1000 | head -5   # GR3D_FREQ should rise during inference
```

Models live at `/usr/share/ollama/.ollama` on the host and are
bind-mounted into the container, so this swap doesn't lose any models
you already pulled.

See [`docs/SETUP.md`](docs/SETUP.md) for the full debugging trail of how
this finding came up during the first install.

## Verifying

```bash
# Service up?
ssh pichick@192.168.0.18 systemctl is-active jetson-pichick
ssh pichick@192.168.0.18 journalctl -u jetson-pichick -n 50 --no-pager

# Subjects arriving on the broker?
nats sub 'home.coop.>'

# What does the GPU look like under load?
ssh pichick@192.168.0.18 tegrastats --interval 2000
```

A healthy deploy emits `status.startup` within a few seconds of
`systemctl start`, `status.tegra` within a minute, and one of
`inference.gated` / `inference.fired` per kept frame.

## Where to read more

- [`src/jchick/app.py`](src/jchick/app.py) — the three async loops
- [`src/jchick/cascade.py`](src/jchick/cascade.py) — gate vs detail decision
- [`src/jchick/diff.py`](src/jchick/diff.py) — pre-LLM frame-delta gate
- [`src/jchick/ollama.py`](src/jchick/ollama.py) — Ollama JSON-mode client
- [`docs/SETUP.md`](docs/SETUP.md) — first-time install walkthrough
