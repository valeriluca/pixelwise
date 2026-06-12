#!/bin/bash
set -euo pipefail

sudo apt update
sudo mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/conf.d
sudo apt install -y git python3 python3-pip python3-venv curl postgresql nginx

# --- Python venv ---
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# --- PostgreSQL: user + database ---
if [ -f .env ]; then
    set -a; source .env; set +a
fi
DB_PW="${DB_PASSWORD:-secret}"
sudo -u postgres psql -tAc \
  "SELECT 1 FROM pg_roles WHERE rolname='pixelwise'" \
  | grep -q 1 || \
  sudo -u postgres psql -c \
  "CREATE USER pixelwise WITH PASSWORD '$DB_PW';"
sudo -u postgres psql -tAc \
  "SELECT 1 FROM pg_database WHERE datname='pixelwise'" \
  | grep -q 1 || \
  sudo -u postgres createdb -O pixelwise pixelwise

# --- Pull model artefact ---
if [ -n "${MODEL_REPO:-}" ] && [ -n "${MODEL_VERSION:-}" ]; then
    mkdir -p models/
    rm -rf /tmp/pixelwise-model
    git clone --depth 1 --branch "$MODEL_VERSION" \
        "$MODEL_REPO" /tmp/pixelwise-model
    cp /tmp/pixelwise-model/*.pkl models/
    cp /tmp/pixelwise-model/MODELCARD.md models/
    rm -rf /tmp/pixelwise-model
fi

# --- Create table via Python ---
source .venv/bin/activate
python3 -c "from app.models import Base, engine; Base.metadata.create_all(engine)"

# --- Nginx + Frontend ---
sudo cp deploy/nginx-pixelwise /etc/nginx/sites-available/pixelwise
sudo ln -sf /etc/nginx/sites-available/pixelwise /etc/nginx/sites-enabled/pixelwise
sudo rm -f /etc/nginx/sites-enabled/default
sudo mkdir -p /var/www/pixelwise
sudo cp -r frontend/* /var/www/pixelwise/
if [ -f .env ]; then
    KEY=$(grep '^SECRET_API_KEY=' .env | cut -d= -f2 | tr -d '\r\n ')
    sudo sed -i "s|^const API_KEY.*|const API_KEY = \"$KEY\";|" /var/www/pixelwise/app.js
fi
sudo nginx -t && sudo systemctl restart nginx

# --- systemd service ---
if [ -f deploy/pixelwise.service ]; then
    sudo cp deploy/pixelwise.service /etc/systemd/system/pixelwise.service
    sudo systemctl daemon-reload
    sudo systemctl enable pixelwise
    sudo systemctl restart pixelwise
    sudo systemctl status pixelwise --no-pager
fi
