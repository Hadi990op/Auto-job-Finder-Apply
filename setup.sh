#!/bin/bash
# ============================================================
# Auto Job Finder & Apply — One-Command Setup Script
# ============================================================
# Clones, installs dependencies, sets up systemd services,
# and starts the agent. Works on any Debian/Ubuntu VM.
# ============================================================
set -e

echo "============================================"
echo "  Auto Job Finder & Apply — Setup"
echo "============================================"

# --- Config ---
INSTALL_DIR="${1:-/opt/job-agent}"
VENV_DIR="${2:-/opt/venv-jobagent}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# --- Check root ---
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo ./setup.sh"
  exit 1
fi

# --- Install system dependencies ---
echo ""
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl wget \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2t64 2>/dev/null || \
apt-get install -y -qq libasound2 2>/dev/null || true

# --- Create directories ---
echo ""
echo "[2/7] Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data/cvs"
mkdir -p "$INSTALL_DIR/data/whatsapp"
mkdir -p "$VENV_DIR"

# --- Copy source files (if running from repo) ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/src/app.py" ]; then
  echo ""
  echo "[3/7] Copying source files..."
  cp -r "$SCRIPT_DIR/src/"* "$INSTALL_DIR/"
  cp "$SCRIPT_DIR/src/data/whatsapp/config.json" "$INSTALL_DIR/data/whatsapp/config.json" 2>/dev/null || true
else
  echo ""
  echo "[3/7] Source files already in place at $INSTALL_DIR"
fi

# --- Create Python venv ---
echo ""
echo "[4/7] Setting up Python virtual environment..."
$PYTHON_BIN -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q 2>/dev/null || \
"$VENV_DIR/bin/pip" install fastapi uvicorn playwright python-multipart httpx requests beautifulsoup4 lxml -q

# --- Install Playwright Chromium ---
echo ""
echo "[5/7] Installing Playwright Chromium (for WhatsApp)..."
"$VENV_DIR/bin/playwright" install chromium
"$VENV_DIR/bin/playwright" install-deps chromium 2>/dev/null || true

# --- Install systemd services ---
echo ""
echo "[6/7] Installing systemd services..."
# Update paths in service files
for svc in job-agent.service job-agent-loop.service; do
  if [ -f "$SCRIPT_DIR/deploy/$svc" ]; then
    sed "s|/opt/baal-agent/workspace/job-agent|$INSTALL_DIR|g; s|/opt/venv-jobagent|$VENV_DIR|g" \
      "$SCRIPT_DIR/deploy/$svc" > "/etc/systemd/system/$svc"
    echo "  Installed $svc"
  fi
done

systemctl daemon-reload
systemctl enable job-agent.service job-agent-loop.service 2>/dev/null

# --- Start services ---
echo ""
echo "[7/7] Starting services..."
systemctl restart job-agent.service
sleep 2
systemctl restart job-agent-loop.service
sleep 1

# --- Done ---
echo ""
echo "============================================"
echo "  SETUP COMPLETE!"
echo "============================================"
echo ""
echo "  Dashboard:  http://localhost:9300"
echo "  API:        http://localhost:9300/api"
echo ""
echo "  Services:"
echo "    job-agent      — Web Dashboard (port 9300)"
echo "    job-agent-loop — Autonomous loop (every 6h)"
echo ""
echo "  Commands:"
echo "    systemctl status job-agent"
echo "    systemctl status job-agent-loop"
echo "    journalctl -u job-agent -f"
echo ""
echo "  Next steps:"
echo "    1. Open http://localhost:9300 in browser"
echo "    2. Set up your profile (name, skills, experience)"
echo "    3. Go to /whatsapp and connect WhatsApp (pair with code)"
echo "    4. The agent will auto-run every 6 hours"
echo ""
echo "============================================"
