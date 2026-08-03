# SETUP — jetson-pichick on the new Jetson Orin Nano

This is the **actual runbook**, captured live during the first deploy on
2026-06-13 against a Jetson Orin Nano dev kit at `pichick@192.168.0.18`.
Reproduce it in order on a fresh JetPack install. Every command shown
here is one we actually ran; every finding is one we actually hit.

---

## Target hardware

| | |
|---|---|
| Board | NVIDIA Jetson Orin Nano dev kit |
| OS | Ubuntu 24.04.4 LTS (`noble`) |
| Kernel | `6.8.12-1021-tegra` |
| L4T | `R39 REV 2.0` (JetPack build dated 2026-06-01) |
| CPU | aarch64, 6 cores |
| RAM | 7.5 GB shared CPU+GPU (unified memory) |
| Storage | 915 GB NVMe, 23 GB used at clean install |
| GPU | Orin (nvgpu), CUDA compute capability **8.7** (`sm_87`) |
| Driver | NVIDIA 595.78 (host-side `nvgpu` kernel module) |
| CUDA on host | 13.2 (per `nvidia-smi`) |

## Required network resources

| Resource | Why we need it |
|---|---|
| `repo.download.nvidia.com/jetson/common r39.2 main` | JetPack apt repo. Already configured by JetPack flash; do not remove. |
| `go.dev/dl/go1.24.4.linux-arm64.tar.gz` | Ollama needs Go ≥ 1.24; Ubuntu 24.04 apt only has 1.22. |
| `github.com/ollama/ollama` | Ollama source for the recompile. |
| `registry.ollama.ai` | Vision models. Each is 1.7-5.5 GB; budget time. |
| Dev box on the LAN | NATS broker; `192.168.0.22:4222` in our setup. |

---

## Component layout (steady state, after all steps below)

| Component | Location | Notes |
|---|---|---|
| Ollama binary | `/usr/local/bin/ollama` | **Native recompile for `sm_87`** — see Step 4 |
| Ollama systemd unit | `/etc/systemd/system/ollama.service` + `ollama.service.d/lan.conf` | LAN bind drop-in we add in Step 1 |
| Ollama models | `/usr/share/ollama/.ollama/models/` | `llava:7b`, `llava-phi3:3.8b`, `llava-llama3:8b`, `moondream:1.8b` |
| jetson-pichick code | `/opt/jchick/` | rsync'd from host repo |
| jetson-pichick venv | `/opt/jchick/.venv/` | `httpx`, `nats-py`, `pillow`, `pyyaml` |
| jetson-pichick env | `/etc/jchick/jchick.env` | mode 0640, owner `root:jchick` |
| jetson-pichick state | `/var/lib/jchick/` | synthetic frames dir, future capture dir |
| jetson-pichick service | `/etc/systemd/system/jetson-pichick.service` | runs as `jchick:jchick` |
| Ollama build tree | `~/src/ollama/` (in `pichick` user home) | only kept until binary is installed |

---

## Step 0 — SSH access

The Jetson came up as `pichick@192.168.0.18` with passwordless SSH
already keyed. If you're reproducing on a fresh box, key it first:
`ssh-copy-id pichick@<jetson-ip>`.

```bash
ssh pichick@192.168.0.18 'uname -a; cat /etc/nv_tegra_release | head -1'
```

Confirm `tegra` in the kernel string and an `R39` (or whatever JetPack
release) line. If the kernel doesn't say `tegra`, you flashed the wrong
image and most of this doc won't apply.

---

## Step 1 — Bind Ollama to the LAN

Stock Ollama installs (`curl -fsSL https://ollama.com/install.sh | sh`)
listen on `127.0.0.1:11434` only. Even when the inference is local to
the Jetson, the dev box's `nats sub` and any external probes need to hit
the API. Add a systemd drop-in:

```bash
ssh pichick@192.168.0.18 'sudo mkdir -p /etc/systemd/system/ollama.service.d && \
  sudo tee /etc/systemd/system/ollama.service.d/lan.conf > /dev/null <<EOF
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama'
```

Verify from your dev box:

```bash
curl -fsS http://192.168.0.18:11434/api/tags | python3 -m json.tool | head
```

Should return JSON, not connect-refused.

---

## Step 2 — Pull the vision models

These must be present before `jetson-pichick` starts; the service does
not auto-pull.

```bash
ssh pichick@192.168.0.18 'ollama pull moondream:1.8b'      # gate, 1.7 GB
ssh pichick@192.168.0.18 'ollama pull llava:7b'            # detail, 4.7 GB
ssh pichick@192.168.0.18 'ollama pull llava-phi3:3.8b'     # alt detail, 2.9 GB
ssh pichick@192.168.0.18 'ollama pull llava-llama3:8b'     # alt detail, 5.5 GB
```

**Do not pull `llava:13b` or anything bigger than ~5 GB.** The box has
7.5 GB total RAM shared between CPU and GPU. Models bigger than ~5 GB
push the kernel into swap during inference, and the dev kit has zero
swap configured by default.

Verify:

```bash
ssh pichick@192.168.0.18 'ollama list'
```

---

## Step 3 — Verify the GPU isn't engaged (and find out why)

This step is informational. Skip if you don't care, but do it once so
you understand the system you're working with.

Stock Ollama on JetPack r39 will run **on CPU only**. From `journalctl
-u ollama --no-pager` after a model load:

```
skipping CUDA device — compute capability not in compiled architectures
device=Orin cc=870 archs="[500 520 600 610 700 750 800 860 890 900 1200]"
```

Stock Ollama for ARM64 ships CUDA kernels for compute caps 8.0 (A100),
8.6 (A40 / GA10x desktop), 8.9 (Ada Lovelace), 9.0 (Hopper) — but not
**8.7**, which is what the Orin SoC reports. Ollama detects the GPU,
finds no kernel for `cc=870`, falls back to CPU, and runs ~16 t/s on
moondream:1.8b.

**Container detour we tried and abandoned (so you don't):**

We tried `dustynv/ollama:r36.4.0` (the JetPack-aware container from
NVIDIA's `jetson-containers` project). All published `dustynv/ollama`
tags target L4T r35 or r36; **there is no r39 tag at the time of
writing**. Running the r36.4.0 image on this r39 host failed with:

```
Unable to load cudart library /usr/local/cuda/compat/libcuda.so.1.1:
  cuda driver library init failure: 801
Unable to load cudart library /usr/lib/aarch64-linux-gnu/nvidia/libcuda.so.1.1:
  file too short
no compatible GPUs were discovered
```

Root cause: the host's actual `libcuda.so.1.1` lives at
`/opt/nvidia/l4t-gpu-libs/nvgpu/` (r39 layout); the container expected
to find it at `/usr/lib/aarch64-linux-gnu/nvidia/` (r36 layout).
nvidia-container-toolkit 1.19.1 bind-mounts the r39 paths but the r36
container userspace doesn't know to look there.

**Conclusion: don't waste time on dustynv until they ship an `r39` tag.
Recompile Ollama natively against the r39 driver instead.**

---

## Step 4 — Native recompile of Ollama for `sm_87`

This is the only path that reliably engages the Orin GPU on JetPack r39
today.

### 4a — Install build prerequisites

```bash
# CUDA toolkit 13.2 (matches the host's CUDA Version: 13.2 from nvidia-smi)
# plus cmake / build tools / git / ccache for incremental rebuilds.
ssh pichick@192.168.0.18 '
  sudo apt-get update -qq &&
  sudo apt-get install -y \
    cmake build-essential git ccache \
    cuda-toolkit-13-2'
```

The `cuda-toolkit-13-2` meta-package pulls about 30 sub-packages
(`cuda-nvcc-13-2`, `libnvvm-13-2`, `cuda-cudart-dev-13-2`,
`libnvptxcompiler-13-2`, etc.) totaling roughly 1.5 GB download.

Add CUDA to the build environment's PATH:

```bash
ssh pichick@192.168.0.18 'echo "export PATH=/usr/local/cuda-13.2/bin:\$PATH
export LD_LIBRARY_PATH=/usr/local/cuda-13.2/lib64:\$LD_LIBRARY_PATH" \
  | sudo tee /etc/profile.d/cuda.sh
sudo chmod +x /etc/profile.d/cuda.sh'
```

Verify:

```bash
ssh pichick@192.168.0.18 'source /etc/profile.d/cuda.sh && nvcc --version'
```

Expect `Cuda compilation tools, release 13.2, ...`.

### 4b — Install Go 1.24

Ollama needs Go ≥ 1.24. Ubuntu 24.04 apt only ships 1.22, so use the
official tarball.

```bash
ssh pichick@192.168.0.18 '
  cd /tmp &&
  curl -fsSL https://go.dev/dl/go1.24.4.linux-arm64.tar.gz -o go.tar.gz &&
  sudo rm -rf /usr/local/go &&
  sudo tar -C /usr/local -xzf go.tar.gz &&
  sudo ln -sf /usr/local/go/bin/go /usr/local/bin/go &&
  sudo ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt &&
  go version'
```

Expect `go version go1.24.4 linux/arm64`.

### 4c — Build Ollama

```bash
ssh pichick@192.168.0.18 '
  source /etc/profile.d/cuda.sh &&
  mkdir -p ~/src && cd ~/src &&
  if [ ! -d ollama ]; then git clone https://github.com/ollama/ollama.git; fi &&
  cd ollama &&
  git fetch --tags && git checkout v0.30.8 &&
  cmake -B build . \
    -DOLLAMA_LLAMA_BACKENDS=cuda_jetpack6 \
    -DCMAKE_CUDA_ARCHITECTURES=87 &&
  cmake --build build --parallel $(nproc) &&
  go build -o ollama .'
```

Build time: **2-3 hours** on the Orin Nano dev kit (6 ARM cores at
~1.5 GHz), dominated by GGML CUDA kernels. With `ccache` warm,
incremental rebuilds drop to 5-10 minutes.

Two flags worth understanding:

- `-DOLLAMA_LLAMA_BACKENDS=cuda_jetpack6` — tells Ollama's build system
  to use the JetPack 6 backend variant (which uses Tegra-aware libcuda
  paths and is the official Ollama-supported way to target Jetson).
  Other valid values per the Ollama dev docs:
  `cuda_v12`, `cuda_v13`, `rocm_v7_1`, `rocm_v7_2`, `vulkan`,
  `cuda_jetpack5`, `cuda_jetpack6`.
- `-DCMAKE_CUDA_ARCHITECTURES=87` — narrows the build to only emit
  kernels for `sm_87` (Orin). Skipping unused architectures cuts build
  time by roughly 4x and binary size by similar.

### 4d — Install the new binary

The `cmake --build` step **will exit non-zero** because of an unavoidable
GCC-13/SME failure on the `armv9.2_2` CPU variant (see Finding 7 below).
That failure happens mid-way; everything we actually need (the Go
binary, `llama-server`, the CUDA libs, and `libggml-base.so`) finishes
building before the failure point. The build script ignores the exit
code at install time for this reason.

```bash
ssh pichick@192.168.0.18 '
  cd ~/src/ollama &&
  sudo systemctl stop ollama &&

  # 1. main binary
  sudo install -m 0755 ollama /usr/local/bin/ollama &&

  # 2. runtime payload — assembled from TWO build subtrees because the
  #    install step never ran (cmake exited non-zero before it).
  sudo rm -rf /usr/local/lib/ollama &&
  sudo mkdir -p /usr/local/lib/ollama &&
  #    a) top-level: libggml-base + per-CPU variants + helpers
  sudo cp -a build/llama-server-local/bin/libggml-base.so* /usr/local/lib/ollama/ &&
  sudo cp -a build/llama-server-local/bin/libggml-cpu-*.so /usr/local/lib/ollama/ &&
  sudo cp -a build/lib/ollama/llama-server /usr/local/lib/ollama/ &&
  sudo cp -a build/lib/ollama/libggml.so* build/lib/ollama/libllama*.so* \
             build/lib/ollama/libmtmd.so* build/lib/ollama/llama-quantize \
             /usr/local/lib/ollama/ 2>/dev/null || true &&
  #    b) cuda_jetpack6 subdir
  sudo cp -ra build/lib/ollama/cuda_jetpack6 /usr/local/lib/ollama/ &&

  sudo chown -R root:root /usr/local/lib/ollama &&
  sudo systemctl start ollama'
```

The build script (`scripts/build-ollama-jetson.sh`) does this assembly
automatically; the snippet above is what you'd run by hand if you built
without the script.

### 4e — Tell Ollama this is JetPack 6 (the second non-obvious step)

Even with the binary correctly built and installed, Ollama will **not**
detect the GPU on JetPack r39 without an env var override. From
`journalctl -u ollama` with `OLLAMA_DEBUG=1`:

```
DEBUG source=gpu.go:63 msg="unrecognized L4T version"
   nv_tegra_release="# R39 (release), REVISION: 2.0, ..."
DEBUG source=runner.go:97 msg="jetpack not detected
   (set JETSON_JETPACK or OLLAMA_LLM_LIBRARY to override), skipping"
   libDir=/usr/local/lib/ollama/cuda_jetpack6
```

Ollama's GPU bootstrap reads `/etc/nv_tegra_release`, parses an `R##`
version, and matches it against a hardcoded table. R39 isn't in that
table (it knows R32/R35/R36). When the version is unrecognized, the
runner refuses to load the cuda_jetpack6 backend even when the libraries
are present and dlopen-able.

Fix: set `JETSON_JETPACK=6` via a systemd drop-in.

```bash
ssh pichick@192.168.0.18 'sudo tee /etc/systemd/system/ollama.service.d/jetson.conf > /dev/null <<EOF
[Service]
Environment="JETSON_JETPACK=6"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama'
```

This single env var is what flips Ollama from "100% CPU" to "100% GPU"
on the Orin. Without it, all the recompile work is invisible at
runtime.

### 4f — Verify the GPU is now in use

```bash
# Drive an inference and watch for GPU usage in two terminals:

# T1: trigger an inference
ssh pichick@192.168.0.18 \
  'ollama run llava:7b "describe this image" < /var/lib/jchick/synthetic/test.jpg'

# T2: watch tegrastats — GR3D_FREQ should rise above 0% during inference
ssh pichick@192.168.0.18 'tegrastats --interval 1000'

# Confirm Ollama is loading models on the GPU, not CPU:
ssh pichick@192.168.0.18 'ollama ps'
# PROCESSOR column should now show e.g. "100% GPU" instead of "100% CPU"

# And the journal should explicitly say so:
ssh pichick@192.168.0.18 'sudo journalctl -u ollama --since "30 seconds ago" \
  | grep "inference compute"'
# expected:
#   inference compute id=0 library=CUDA compute=8.7 name=CUDA0
#   description=Orin libdirs=ollama,cuda_jetpack6 driver=13.2
#   total="7.3 GiB" available="6.2 GiB"
```

Measured on this hardware (Orin Nano, after recompile + JETSON_JETPACK):

| Model | First call (incl. load) | Warm gen rate | Peak GR3D% |
|---|---|---|---|
| `moondream:1.8b` | 12.5 s | 32.4 t/s | 99% |
| `llava:7b` | 24.2 s (14 s load + 8 s prompt + 2 s gen) | 11.3 t/s | 99% |

For comparison, **before the recompile** the same `llava:7b` ran at
~16 t/s end-to-end on CPU and stayed there — the new build is meaningfully
faster on the small model and on warm-up paths, with the heavy lifting
(image encode + prompt eval) still being the dominant cost on the 7B
vision path.

---

## Step 5 — Push and install jetson-pichick

```bash
rsync -av --delete \
  --exclude .venv --exclude __pycache__ --exclude .git --exclude .omo \
  ~/code/jetson-pichick/ pichick@192.168.0.18:/tmp/jetson-pichick/

ssh pichick@192.168.0.18 'sudo bash /tmp/jetson-pichick/scripts/install.sh'
```

The install script is **idempotent** (safe to re-run after a code
update). What it does, in order:

1. `apt-get install -y python3-venv python3-pip ffmpeg v4l-utils`
2. Creates the `jchick:jchick` system user (no shell, home is
   `/opt/jchick`).
3. Adds `jchick` to the `video` group so it can read `/dev/video*`.
4. Creates `/opt/jchick`, `/var/lib/jchick`, `/var/lib/jchick/synthetic`
   (mode 0755, owner `jchick:jchick`); `/etc/jchick` (mode 0755, owner
   `root:root`).
5. `rsync`'s the repo into `/opt/jchick/` (excluding `.git`, `.venv`,
   `__pycache__`, etc.), then `chown -R jchick:jchick /opt/jchick`.
6. Seeds `/etc/jchick/jchick.env` from `.env.example` if not present
   (mode 0640, owner `root:jchick`). Re-runs do **not** clobber an
   existing env file unless `INSTALL_UPDATE_ENV=1` is set (see below).
7. `python3 -m venv /opt/jchick/.venv`, then
   `pip install -e /opt/jchick` (pulls `httpx`, `nats-py`, `pillow`,
   `pyyaml`).
8. Installs `assets/jetson-pichick.service` to
   `/etc/systemd/system/`, runs `systemctl daemon-reload && systemctl
   enable`.
9. Writes `/etc/tmpfiles.d/jchick.conf` so `/run/jchick` exists across
   reboots.

It does **not**: install or configure Ollama (already done in step 1
and 4), pull models (step 2), or start the service (step 7).

### Updating env config on an already-installed device

By default `install.sh` leaves `/etc/jchick/jchick.env` alone once it
exists — so a code re-deploy can't silently overwrite hand-edited
per-device values like `JCHICK_CAPTURE_DEVICE=/dev/video1`.

When you've changed defaults in `.env.example` and want to push them to
an installed device, opt in with `INSTALL_UPDATE_ENV=1`:

```bash
ssh pichick@192.168.0.18 'sudo INSTALL_UPDATE_ENV=1 \
  bash /tmp/jetson-pichick/scripts/install.sh'
```

This backs up the existing file to `/etc/jchick/jchick.env.bak` and
re-seeds from `.env.example`. Per-device overrides then need to be
re-applied by hand (`sudo vi /etc/jchick/jchick.env`).

---

## Step 6 — Configure

```bash
ssh pichick@192.168.0.18 'sudo vi /etc/jchick/jchick.env'
```

Defaults from `.env.example` are correct for our setup:

```
NATS_URL=nats://192.168.0.22:4222     # your dev box's LAN IP
OLLAMA_URL=http://127.0.0.1:11434     # local Ollama on the Jetson
JCHICK_GATE_MODEL=moondream:1.8b
JCHICK_DETAIL_MODEL=llava:7b
JCHICK_CAPTURE_SOURCE=synthetic       # change to v4l2 once camera is plugged in
JCHICK_CAPTURE_DEVICE=/dev/video0
JCHICK_CAPTURE_FPS=1.0
JCHICK_CAPTURE_WIDTH=1280
JCHICK_CAPTURE_HEIGHT=720
JCHICK_DIFF_THRESHOLD=0.015
JCHICK_HEARTBEAT_SECONDS=300
JCHICK_TEGRASTATS_SECONDS=60
```

Required vars: `NATS_URL`, `OLLAMA_URL`. Everything else has a default.

---

## Step 7 — Start NATS broker on the dev box

The chicken-cam node publishes to NATS. The broker on the Mac/dev box
must be running before you `systemctl start jetson-pichick`, or you'll
see a flurry of reconnect-error tracebacks in the journal until it
becomes available (the service self-heals once NATS comes up — see
finding 4 below — but the noise is avoidable).

```bash
# On the dev box (macOS):
brew install nats-server   # if not already installed
nats-server -p 4222 &
lsof -nP -iTCP:4222 -sTCP:LISTEN  # confirm something is listening
```

For persistence across Mac reboots, set up a launchd plist or use
`brew services start nats-server`.

---

## Step 8 — Start jetson-pichick

```bash
ssh pichick@192.168.0.18 'sudo systemctl start jetson-pichick'
ssh pichick@192.168.0.18 'sudo systemctl is-active jetson-pichick'  # → active
ssh pichick@192.168.0.18 'sudo journalctl -u jetson-pichick -f'
```

A healthy startup looks like:

```
INFO jchick.app: jchick: starting host=pichick ollama=http://127.0.0.1:11434 nats=nats://192.168.0.22:4222 gate=moondream:1.8b detail=llava:7b capture=synthetic@1.00fps
INFO jchick.nats_pub: nats: connected to nats://192.168.0.22:4222
INFO httpx: HTTP Request: POST http://127.0.0.1:11434/api/generate "HTTP/1.1 200 OK"
```

---

## Step 9 — Subscribe to the broker and watch traffic flow

The `nats` CLI brew formula has been broken on macOS 26 (we hit it
during this install — see finding 5). Workaround: use Python `nats-py`
from the existing `piChick` venv:

```bash
~/code/piChick/.venv/bin/python -c "
import asyncio, json
from nats.aio.client import Client as NATS
async def main():
    nc = NATS()
    await nc.connect('nats://127.0.0.1:4222')
    async def cb(msg):
        try: payload = json.loads(msg.data.decode())
        except Exception: payload = msg.data.decode(errors='replace')
        print(msg.subject, '|', json.dumps(payload, default=str)[:240])
    await nc.subscribe('home.coop.>', cb=cb)
    print('subscribed; ctrl-c to quit')
    await asyncio.Event().wait()
asyncio.run(main())
"
```

You should see at minimum `home.coop.pichick.status.startup` and
`home.coop.pichick.status.tegra` within ~60 s, and one of
`home.coop.pichick.inference.{gated,fired}` per kept frame.

---

## Step 10 — Move the Jetson to the coop and connect a real camera

When you physically relocate the Jetson and plug in the USB camera,
two changes:

```bash
# 1. flip capture source from synthetic to v4l2
ssh pichick@<jetson-ip> 'sudo sed -i \
  "s|^JCHICK_CAPTURE_SOURCE=.*|JCHICK_CAPTURE_SOURCE=v4l2|" \
  /etc/jchick/jchick.env'

# 2. confirm the camera shows up (and at which device path)
ssh pichick@<jetson-ip> 'v4l2-ctl --list-devices'

# 3. if the camera is at /dev/video1 instead of /dev/video0, update the env file
#    JCHICK_CAPTURE_DEVICE=/dev/video1

# 4. restart
ssh pichick@<jetson-ip> 'sudo systemctl restart jetson-pichick'
```

**No code changes** required. The same binary, same systemd unit, same
venv that ran with the synthetic source picks up `/dev/video0` once the
env var flips. If you want to rule out a camera issue while
troubleshooting, flip back to `synthetic` and confirm the rest of the
pipeline still works.

---

## Findings discovered during this install

These are real issues we hit. Capturing them so the next person doesn't
have to re-discover them.

### Finding 1: `dustynv/ollama` does not have an r39 tag

`dustynv/ollama` (NVIDIA's `jetson-containers` project, the
"recommended" path on most Jetson tutorials) ships images for L4T r35
and r36. The dev kit ships images dated 2026-06-01 ship JetPack r39 by
default. Container's r36-era driver shim paths
(`/usr/lib/aarch64-linux-gnu/nvidia/libcuda.so.1.1`) don't match what
the host has at `/opt/nvidia/l4t-gpu-libs/nvgpu/`, and
nvidia-container-toolkit's bind-mount doesn't paper over the gap. The
container reports `no compatible GPUs were discovered` and falls back
to CPU. **Don't bother with the container path until they ship an r39
tag** (track <https://hub.docker.com/r/dustynv/ollama/tags>).

### Finding 2: Stock Ollama's published ARM64 binary skips `sm_87`

Quoted from `journalctl -u ollama` on this exact box:

```
skipping CUDA device — compute capability not in compiled architectures
device=Orin cc=870 archs="[500 520 600 610 700 750 800 860 890 900 1200]"
```

Workarounds: native recompile (Step 4 above) or wait for upstream to
add 870 to its arch list. Recompile is the only available path today.

### Finding 3: Ollama needs Go ≥ 1.24, Ubuntu 24.04 ships 1.22

Don't try `apt install golang-go` for the Ollama build — you'll get
1.22 and the Go module graph won't resolve. Use the official Go tarball
from `go.dev/dl/`. Step 4b above is the one we ran.

### Finding 4: jchick recovers from NATS being unavailable at startup

If you start `jetson-pichick` before `nats-server` is up, you'll see
ugly tracebacks in the journal:

```
ERROR nats.aio.client: nats: encountered error
TimeoutError
```

The service does **not** crash. `nats-py` is configured with
`allow_reconnect=True, max_reconnect_attempts=-1`, and it retries every
~7 seconds. The moment NATS comes up, the next retry succeeds and you
get:

```
INFO jchick.nats_pub: nats: connected to nats://192.168.0.22:4222
```

The inference loop keeps running through this — frames are processed
on-Ollama and results are dropped from the publish call (which is a
no-op when `nc.is_connected` is False). No queue, no replay; missed
frames are just missed.

### Finding 5: `brew install nats-io/nats-tools/nats` fails on macOS 26

The CLI formula raises a Ruby error in
`formula_installer.rb:1145` during install. The `nats-server` formula
itself works fine. Workaround: use the Python `nats-py` from the
existing `piChick` venv (Step 9 above). If you really want the CLI,
pull a release binary directly from
<https://github.com/nats-io/natscli/releases>.

### Finding 6: 1 fps capture saturates a CPU-only Ollama under cascade

With CPU-only inference (gate ~10 s, detail ~30 s) and `JCHICK_CAPTURE_FPS=1.0`,
the capture loop produces frames faster than the cascade can drain them.
Multiple httpx requests pile up against Ollama; eventually httpx times
out individual calls and we see:

```
WARNING jchick.app: inference failed: ollama transport error: All connection attempts failed
```

This **largely self-resolves on GPU** (5-10× speedup brings inference
under the 1 s capture period). If it still happens after the GPU
recompile, options are:

1. Drop `JCHICK_CAPTURE_FPS` (e.g. to 0.2 = one frame every 5 s).
2. Add a single-flight lock in `cascade.py` so only one inference is
   in flight at a time and the capture loop blocks instead of queuing.
3. Switch to motion-gated capture (port the `MotionHookListener` from
   piChick v1 if/when we plug in Motion).

We're tracking this and will add (2) if it survives the GPU bring-up.

### Finding 7: GCC 13.3 + ggml's `armv9.2_2 + SME` CPU variant won't compile

Mid-way through `cmake --build`, the build hits:

```
cc1: error: invalid feature modifier 'sme' in
   '-march=armv9.2-a+dotprod+fp16+sve+i8mm+sve2+sme'
cc1: note: valid arguments are: ... sm4; did you mean 'sm4'?
gmake[6]: *** [...ggml-cpu-armv9.2_2.dir/build.make:76] Error 1
```

ggml's CMake autodetects that the Orin SoC reports armv9.2-a and tries
to enable the SME (Scalable Matrix Extension) march modifier. GCC 13.3
(Ubuntu 24.04 default) doesn't understand SME — that landed in GCC 14.

Workaround: `-DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=OFF`. With both
flags, ggml builds **one** CPU variant matching the host compiler instead
of enumerating armv8.0 / armv8.2 / armv9.2 variants. We don't lose
anything that matters because we're targeting the GPU; the CPU path
only runs the runner glue.

`GGML_NATIVE=OFF` alone is **not enough** — when `GGML_CPU_ALL_VARIANTS`
is on (its default for the cuda_jetpack6 backend), ggml still tries every
variant. Both flags are required.

The build still exits non-zero because the failed armv9.2_2 target
propagates an `Error 2` up through gmake at the very end. By that point
all the artifacts we need (Go binary, `llama-server`, `libggml-cuda.so`,
the per-CPU `libggml-cpu-*.so` files for the variants that did compile)
are already on disk. The build script copes with this and does the
install regardless.

### Finding 8: Ollama silently disables JetPack support on unrecognized L4T versions

Even with the recompiled binary in place and the right libraries on
disk, Ollama refuses to use the GPU on JetPack r39 unless you explicitly
tell it which JetPack generation it's running on. The runtime parses
`/etc/nv_tegra_release`, sees `R39`, fails to match it against its
hardcoded list, and silently skips loading the `cuda_jetpack6` backend.

Fix: `Environment="JETSON_JETPACK=6"` in a systemd drop-in. See Step 4e.
This is the second non-obvious gate; without it the recompile work is
invisible at runtime and `inference compute` reports `library=cpu`.

### Finding 9: Build artifacts scatter across two subtrees that the failed install never merged

`OLLAMA_LLAMA_BACKENDS=cuda_jetpack6` actually compiles **two** runtime
trees, both meant to land in `lib/ollama/` after the install step:

- `build/llama-server-local/bin/` — generic / fallback runtime
  (`libggml-base.so`, `libggml-cpu-*.so`, etc).
- `build/llama-server-cuda_jetpack6/bin/` — same, plus
  `libggml-cuda.so` and friends.

Both contain a `libggml-base.so` that `libggml-cuda.so` depends on at
runtime. Because the `cmake --install` step gets cancelled by the SME
failure at the very end of the build, neither tree gets merged into
`build/lib/ollama/`. You have to assemble the install layout by hand
(see Step 4d). Stock Ollama solves this with `RPATH=$ORIGIN:$ORIGIN/..`
on `libggml-cuda.so`, but our build's RPATH ends up as just `$ORIGIN`,
so we need either the env var `LD_LIBRARY_PATH=...` workaround OR the
correct flat install layout we ended up with.

### Finding 10: moondream:1.8b loops list items on featureless frames

On synthetic / featureless frames (e.g. a solid-color test image),
moondream's JSON output hits a degenerate token loop:

```
{"chickens": 2, "other_animals": ["chicken", "duck", "goose", "ostrich",
"swan", "pigeon", "rooster", "turkey", "duck", "goose", "ostrich",
"swan", "pigeon", "rooster", "turkey", "duck", "goose", "ostrich", ...
```

It generates the same list of animals over and over until it hits the
default response cap, which leaves the JSON unterminated and
`json.loads` rejects it. The cascade catches the error and treats the
frame as "uninteresting" — correct behavior, but noisy in the log.

Mitigations applied in `src/jchick/ollama.py`:

1. **Single-line schema in the prompt** so the model can't fall into
   "I'm writing a multi-line list" mode.
2. **`num_predict: 256`** caps generation length; the JSON closes before
   the loop runs forever.

This is a **moondream-1.8b** quality issue on out-of-distribution input,
not a pipeline bug. On real coop frames the model behaves better.
`llava-phi3:3.8b` is more robust against this and is a worth a try as
the gate model on real frames.

---

## What success looks like end-to-end

Once everything is in place:

```bash
ssh pichick@192.168.0.18 'sudo systemctl is-active ollama jetson-pichick'
# → active
# → active

ssh pichick@192.168.0.18 'ollama ps'
# NAME              ID    SIZE    PROCESSOR     CONTEXT   UNTIL
# moondream:1.8b    ...   1.2 GB  100% GPU      2048      4 minutes from now
# (or llava:7b at 4.7 GB / 100% GPU when the cascade fires)

ssh pichick@192.168.0.18 'tegrastats --interval 1000 | head -3'
# RAM 3623/7485MB ... GR3D_FREQ 91%@618 ... gpu@53.875C ... VDD_IN 11430mW
# (during inference; idles at GR3D_FREQ 0% between frames)

# On any LAN host that can reach the broker
~/code/piChick/.venv/bin/python -c "<NATS subscribe one-liner from Step 9>"
# home.coop.pichick.status.startup    | {"event":"startup",...}
# home.coop.pichick.status.tegra      | {"gpu_pct":91,"ram_pct":48,"vdd_in_mw":11430,...}
# home.coop.pichick.inference.fired   | {"chickens":3,"detail":{...},...}
# home.coop.pichick.inference.gated   | {"diff_score":0.001,"gate":{...}}
```

If any of these three checks fail, look at the relevant journal:
`journalctl -u ollama -n 100`, `journalctl -u jetson-pichick -n 100`,
or the dev box NATS subscriber output.

### Steady-state runtime configuration on this Jetson

The systemd drop-ins added during this install (all under
`/etc/systemd/system/ollama.service.d/`):

| File | Purpose |
|---|---|
| `lan.conf` | `OLLAMA_HOST=0.0.0.0:11434` — LAN-bind |
| `jetson.conf` | `JETSON_JETPACK=6` — force-enable cuda_jetpack6 backend on r39 host |

`lib.conf` and `debug.conf` (LD_LIBRARY_PATH and OLLAMA_DEBUG) were used
during bring-up but are not needed in steady state — the rebuilt binary
finds its libs via the standard `/usr/local/lib/ollama/` layout. You can
delete them once you've confirmed everything works:

```bash
sudo rm -f /etc/systemd/system/ollama.service.d/lib.conf
sudo rm -f /etc/systemd/system/ollama.service.d/debug.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Equivalent jchick env in `/etc/jchick/jchick.env`:

```
NATS_URL=nats://192.168.0.76:4222     # control-Pi / dedicated broker
OLLAMA_URL=http://127.0.0.1:11434     # local on the Jetson
JCHICK_GATE_MODEL=llava-phi3:3.8b      # more robust than moondream on featureless frames (Finding 10)
JCHICK_DETAIL_MODEL=llava:7b
JCHICK_CAPTURE_SOURCE=synthetic       # → v4l2 once the camera lands at the coop
```
