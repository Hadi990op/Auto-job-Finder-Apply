#!/bin/bash
# Quick one-line installer — downloads repo and runs setup
set -e

REPO_URL="https://github.com/Hadi990op/Auto-job-Finder-Apply.git"
TEMP_DIR="/tmp/auto-job-agent"

echo "Downloading Auto Job Finder & Apply..."
rm -rf "$TEMP_DIR"
git clone --depth 1 "$REPO_URL" "$TEMP_DIR"

cd "$TEMP_DIR"
chmod +x setup.sh
./setup.sh
