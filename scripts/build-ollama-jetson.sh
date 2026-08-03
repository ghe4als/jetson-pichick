#!/usr/bin/env bash
# Build Ollama from source for Jetson Orin sm_87.
#
# Prereqs (must already be installed):
#   - cuda-toolkit-13-2  (apt, from JetPack r39.2 repo)
#   - cmake, build-essential, git, ccache  (apt)
#   - go 1.24+  (from go.dev tarball; apt's golang-go is 1.22, too old)
#
# What it does:
#   1. Sources /etc/profile.d/cuda.sh to get nvcc + libs on PATH/LD path.
#   2. Clones (or updates) github.com/ollama/ollama at v0.30.8 in $HOME/src.
#   3. Configures cmake with cuda_jetpack6 backend + sm_87 only.
#   4. Builds the native runtime (~2-3 hrs cold, 5-10 min warm via ccache).
#   5. Builds the Go binary at the repo root.
#   6. Stops the existing ollama.service, swaps in the new binary +
#      /usr/local/lib/ollama runtime payload, restarts.
#
# Logs to /tmp/ollama-build.log. Re-running is safe and idempotent;
# ccache caches the heavy GGML CUDA kernels across runs.
#
# Usage:
#   nohup bash /tmp/jetson-pichick/scripts/build-ollama-jetson.sh \
#     > /tmp/ollama-build.log 2>&1 &
#   tail -f /tmp/ollama-build.log
set -euo pipefail

OLLAMA_TAG="${OLLAMA_TAG:-v0.30.8}"
SRC_DIR="${SRC_DIR:-$HOME/src/ollama}"
INSTALL_BIN="/usr/local/bin/ollama"
INSTALL_LIB="/usr/local/lib/ollama"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

log "ollama-jetson build starting (tag=$OLLAMA_TAG)"

# ---- prereq check (fail fast, not 2 hours later) -----------------
[ -f /etc/profile.d/cuda.sh ] || {
  log "ERROR: /etc/profile.d/cuda.sh missing — did Step 4a complete?"
  log "       create it with:"
  log "         sudo tee /etc/profile.d/cuda.sh <<EOF"
  log "         export PATH=/usr/local/cuda-13.2/bin:\\\$PATH"
  log "         export LD_LIBRARY_PATH=/usr/local/cuda-13.2/lib64:\\\$LD_LIBRARY_PATH"
  log "         EOF"
  exit 2
}
# shellcheck disable=SC1091
source /etc/profile.d/cuda.sh

command -v nvcc  >/dev/null || { log "ERROR: nvcc not on PATH";  exit 2; }
command -v cmake >/dev/null || { log "ERROR: cmake not installed"; exit 2; }
command -v go    >/dev/null || { log "ERROR: go not installed";  exit 2; }
command -v git   >/dev/null || { log "ERROR: git not installed"; exit 2; }

GO_VER="$(go version | awk '{print $3}' | sed 's/^go//')"
GO_MAJ="${GO_VER%%.*}"; GO_MIN="$(echo "$GO_VER" | cut -d. -f2)"
if [ "$GO_MAJ" -lt 1 ] || { [ "$GO_MAJ" -eq 1 ] && [ "$GO_MIN" -lt 24 ]; }; then
  log "ERROR: Go $GO_VER is too old; need >= 1.24"; exit 2
fi

log "prereqs ok: nvcc=$(nvcc --version | tail -1 | awk '{print $5}' | tr -d ,) go=$GO_VER cmake=$(cmake --version | head -1 | awk '{print $3}')"

# ---- ccache wiring ----------------------------------------------
export CCACHE_DIR="$HOME/.ccache"
export PATH="/usr/lib/ccache:$PATH"   # apt installs ccache symlinks here
log "ccache stats before:"
ccache -s 2>&1 | sed 's/^/  /' | tail -5 || true

# ---- source ------------------------------------------------------
mkdir -p "$(dirname "$SRC_DIR")"
if [ -d "$SRC_DIR/.git" ]; then
  log "updating existing checkout at $SRC_DIR"
  git -C "$SRC_DIR" fetch --tags --quiet
else
  log "cloning ollama into $SRC_DIR"
  git clone --quiet https://github.com/ollama/ollama.git "$SRC_DIR"
fi
git -C "$SRC_DIR" checkout --quiet "$OLLAMA_TAG"
log "checked out $OLLAMA_TAG ($(git -C "$SRC_DIR" rev-parse --short HEAD))"

cd "$SRC_DIR"

# ---- native build ------------------------------------------------
log "configuring cmake (backend=cuda_jetpack6, arch=sm_87, GGML_NATIVE=OFF, GGML_CPU_ALL_VARIANTS=OFF)"
# GGML_CPU_ALL_VARIANTS=OFF forces ggml to build a single CPU backend
# matching the host compiler, instead of enumerating armv8.0/armv8.2/
# armv9.2 variants. The armv9.2_2 variant requires the SME (Scalable
# Matrix Extension) march modifier, which GCC 13.3 (Ubuntu 24.04 default)
# does not understand: it errors with
#   cc1: error: invalid feature modifier 'sme' in '-march=armv9.2-a+...+sme'
# Without this flag the build aborts mid-way, so the llama-server helper
# binary never gets produced and Ollama falls back to CPU-only at runtime.
# GGML_NATIVE=OFF on its own is not enough — ggml still tries every
# variant when CPU_ALL_VARIANTS is on.
cmake -B build . \
  -DOLLAMA_LLAMA_BACKENDS=cuda_jetpack6 \
  -DCMAKE_CUDA_ARCHITECTURES=87 \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF \
  -DGGML_CPU_ALL_VARIANTS=OFF \
  2>&1 | tail -20

NPROC="$(nproc)"
log "building native runtime with $NPROC parallel jobs (this is the long part)"
# The cmake build will exit non-zero because the armv9.2_2 CPU variant
# fails to compile under GCC 13.3 (SME march modifier not understood).
# Everything we actually need is built before that failure. We tolerate
# the non-zero exit and move on; the install step below verifies the
# artifacts we care about are on disk.
cmake --build build --parallel "$NPROC" || log "cmake build exited non-zero (expected — armv9.2_2 SME failure); continuing"
log "native build phase complete"

# ---- go binary --------------------------------------------------
log "go building ollama binary"
go build -o ollama .
log "go build complete"

# ---- install ----------------------------------------------------
log "verifying required artifacts before install"
required=(
  "$SRC_DIR/ollama"
  "$SRC_DIR/build/lib/ollama/llama-server"
  "$SRC_DIR/build/lib/ollama/cuda_jetpack6/libggml-cuda.so"
  "$SRC_DIR/build/llama-server-local/bin/libggml-base.so"
)
for f in "${required[@]}"; do
  if [ ! -e "$f" ]; then
    log "MISSING required artifact: $f"; exit 4
  fi
done
log "all required artifacts present"

log "swapping in new binary + runtime payload"
sudo systemctl stop ollama || true

sudo install -m 0755 "$SRC_DIR/ollama" "$INSTALL_BIN"
sudo rm -rf "$INSTALL_LIB"
sudo mkdir -p "$INSTALL_LIB"

# Top-level layout: libggml-base + per-CPU variants from llama-server-local
sudo cp -a "$SRC_DIR"/build/llama-server-local/bin/libggml-base.so* "$INSTALL_LIB/"
sudo cp -a "$SRC_DIR"/build/llama-server-local/bin/libggml-cpu-*.so "$INSTALL_LIB/"

# Plus the helpers and shared libs that did make it into build/lib/ollama
for f in llama-server llama-quantize libggml.so* libllama-common.so* \
         libllama-quantize-impl.so libllama-server-impl.so libllama.so* \
         libmtmd.so* libggml-cpu.so; do
  matches=( "$SRC_DIR"/build/lib/ollama/$f )
  if [ -e "${matches[0]}" ]; then
    sudo cp -a "$SRC_DIR"/build/lib/ollama/$f "$INSTALL_LIB"/
  fi
done

# CUDA-specific subdir
sudo cp -ra "$SRC_DIR"/build/lib/ollama/cuda_jetpack6 "$INSTALL_LIB/"

sudo chown -R root:root "$INSTALL_LIB"

# JETSON_JETPACK env var: Ollama parses /etc/nv_tegra_release for an R##
# tag and matches against a hardcoded list. r39 is not in that list, so
# the runner refuses to load cuda_jetpack6 even though everything else
# is in place. This drop-in forces it to recognize JetPack 6.
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/jetson.conf > /dev/null <<EOF
[Service]
Environment="JETSON_JETPACK=6"
EOF
sudo systemctl daemon-reload

sudo systemctl start ollama
sleep 3

# ---- smoke test --------------------------------------------------
log "post-install smoke test"
if ! curl -fsS --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null; then
  log "ERROR: ollama HTTP api unreachable after restart"
  sudo journalctl -u ollama -n 30 --no-pager
  exit 3
fi

log "checking GPU detection in journal"
sudo journalctl -u ollama --since "30 seconds ago" --no-pager \
  | grep -iE "gpu|cuda|inference compute|skipping" \
  | head -10 \
  | sed 's/^/  /' || true

log "ccache stats after:"
ccache -s 2>&1 | sed 's/^/  /' | tail -5 || true

log "DONE. Verify GPU is in use with:"
log "  ollama ps                  # PROCESSOR column should say GPU"
log "  tegrastats --interval 1000 # GR3D_FREQ should rise during inference"
