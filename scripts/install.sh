#!/usr/bin/env bash
# jetson-pichick install script. Idempotent: safe to re-run.
#
# Run on the Jetson:
#   sudo bash scripts/install.sh
#
# What it does:
#   1. apt-installs runtime deps (python3-venv, ffmpeg)
#   2. creates the jchick system user and /opt/jchick install dir
#   3. (re)seeds /etc/jchick/jchick.env from .env.example (backs up prior)
#   4. builds a venv at /opt/jchick/.venv and pip-installs this repo
#   5. installs and enables the systemd unit
#
# .env.example is the source of truth for runtime config. Every run
# overwrites /etc/jchick/jchick.env from it (with a .bak backup) so config
# changes ship with a normal install.sh run — no hand-editing on the box.
#
# It does NOT install or configure Ollama. That's expected to be already
# running on this host (loopback or LAN) and pointed to by OLLAMA_URL.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "install.sh: must run as root (sudo)" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/opt/jchick"
ENV_DIR="/etc/jchick"
STATE_DIR="/var/lib/jchick"
RUNTIME_DIR="/run/jchick"
USER="jchick"
GROUP="jchick"

echo "==> apt: installing runtime dependencies"
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3-venv python3-pip ffmpeg v4l-utils gstreamer1.0-tools

echo "==> users: creating $USER (system user)"
if ! getent group "$GROUP" >/dev/null; then
  groupadd --system "$GROUP"
fi
if ! id -u "$USER" >/dev/null 2>&1; then
  useradd --system --gid "$GROUP" \
    --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$USER"
fi
# allow the service to read /dev/video*
usermod -a -G video "$USER" || true

echo "==> dirs: creating $INSTALL_DIR $ENV_DIR $STATE_DIR"
install -d -m 0755 -o "$USER" -g "$GROUP" "$INSTALL_DIR"
install -d -m 0755 -o "$USER" -g "$GROUP" "$STATE_DIR"
install -d -m 0755 -o "$USER" -g "$GROUP" "$STATE_DIR/synthetic"
install -d -m 0755 root:root "$ENV_DIR"

echo "==> code: copying repo to $INSTALL_DIR"
rsync -a --delete \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' \
  "$REPO_DIR"/ "$INSTALL_DIR"/
chown -R "$USER:$GROUP" "$INSTALL_DIR"

echo "==> env: seeding $ENV_DIR/jchick.env from .env.example"
if [[ ! -f "$ENV_DIR/jchick.env" ]]; then
  install -m 0640 -o root -g "$GROUP" "$REPO_DIR/.env.example" \
    "$ENV_DIR/jchick.env"
  echo "    created (mode 0640 root:$GROUP)."
else
  # Always re-seed from .env.example — it is the source of truth for this
  # device's runtime config. Back up the previous file so a bad push can
  # be undone. To keep a hand-edited env on a box, don't run install.sh
  # (or restore from jchick.env.bak afterward).
  cp -a "$ENV_DIR/jchick.env" "$ENV_DIR/jchick.env.bak"
  install -m 0640 -o root -g "$GROUP" "$REPO_DIR/.env.example" \
    "$ENV_DIR/jchick.env"
  echo "    overwritten from .env.example (backup at jchick.env.bak)."
fi

echo "==> venv: building $INSTALL_DIR/.venv"
sudo -u "$USER" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade -q pip
sudo -u "$USER" "$INSTALL_DIR/.venv/bin/pip" install -q -e "$INSTALL_DIR"

echo "==> systemd: installing unit"
install -m 0644 "$REPO_DIR/assets/jetson-pichick.service" \
  /etc/systemd/system/jetson-pichick.service
systemctl daemon-reload
systemctl enable jetson-pichick.service

# tmpfiles entry so /run/jchick is recreated on reboot
cat >/etc/tmpfiles.d/jchick.conf <<EOF
d $RUNTIME_DIR 0755 $USER $GROUP -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/jchick.conf || true

echo
echo "Install complete. Next:"
echo "  1. sudo vi $ENV_DIR/jchick.env       # set NATS_URL etc"
echo "  2. sudo systemctl start jetson-pichick"
echo "  3. journalctl -u jetson-pichick -f"
