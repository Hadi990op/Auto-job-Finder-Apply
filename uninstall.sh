#!/bin/bash
# Quick uninstall — removes services and files
set -e

INSTALL_DIR="${1:-/opt/job-agent}"
VENV_DIR="${2:-/opt/venv-jobagent}"

echo "Stopping services..."
systemctl stop job-agent.service job-agent-loop.service 2>/dev/null || true
systemctl disable job-agent.service job-agent-loop.service 2>/dev/null || true
rm -f /etc/systemd/system/job-agent.service /etc/systemd/system/job-agent-loop.service
systemctl daemon-reload

echo "Removing files..."
rm -rf "$INSTALL_DIR" "$VENV_DIR"

echo "Done. Agent fully removed."
