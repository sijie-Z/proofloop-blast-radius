#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 Vultr Cloud Compute VM for ProofLoop.
# Run as root: sudo bash deploy/bootstrap.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/proofloop-blast-radius}"
REPO_URL="${REPO_URL:-https://github.com/sijie-Z/proofloop-blast-radius.git}"
APP_USER="${APP_USER:-proofloop}"

if [ ! -f /etc/os-release ] || ! grep -qi ubuntu /etc/os-release; then
  echo "This script targets Ubuntu 24.04 (Vultr Cloud Compute)." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  apt-get update
  apt-get install -y ca-certificates curl git python3-venv python3-pip
  curl -fsSL https://get.docker.com | sh
fi

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$APP_USER"
fi
usermod -aG docker "$APP_USER"

if [ ! -d "$APP_DIR/.git" ]; then
  mkdir -p "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "Building the sandbox image..."
docker build -t proofloop-sandbox:latest "$APP_DIR/docker"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/webapp/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/.venv"

install -d -m 750 -o root -g "$APP_USER" /etc/proofloop
if [ ! -f /etc/proofloop/proofloop.env ]; then
  cat > /etc/proofloop/proofloop.env <<'ENV'
# Fill in the Vultr Serverless Inference key, then restart proofloop-web.
VULTR_INFERENCE_API_KEY=
# VULTR_MODEL=deepseek-v4.1-flash-1017
# PROOFLOOP_DEMO_MAX_RUNS_PER_HOUR=20
# PROOFLOOP_DOCKER_RUNTIME=runsc
ENV
  chmod 640 /etc/proofloop/proofloop.env
fi

sed "s|/opt/proofloop-blast-radius|$APP_DIR|g" \
  "$APP_DIR/deploy/proofloop-web.service" \
  > /etc/systemd/system/proofloop-web.service
systemctl daemon-reload
systemctl enable --now proofloop-web.service

echo
echo "ProofLoop web service installed."
echo "1) Put the Vultr key in /etc/proofloop/proofloop.env (mode 640)."
echo "2) sudo systemctl restart proofloop-web"
echo "3) curl http://127.0.0.1:8000/health"
echo "4) Put Caddy in front of 127.0.0.1:8000 (see deploy/Caddyfile and deploy/vultr-vm.md)."
