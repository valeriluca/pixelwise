#!/bin/bash
# deploy/auto-deploy.sh
# Authored on dev, executed on prod by the timer.
# Pull origin/main, gate on pytest, restart the service on green.
set -euo pipefail
cd /opt/pixelwise
git fetch origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi
echo "New commit on main: $REMOTE"
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt > /dev/null
if ! python -m pytest tests/; then
    echo "Tests failed, refusing to deploy."
    exit 1
fi
sudo systemctl restart pixelwise
echo "Deployed: $REMOTE"
