#!/bin/bash
set -euo pipefail

# --- System packages ---
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv curl \
  postgresql nginx-common nginx

# --- PostgreSQL cluster ---
sudo pg_dropcluster --stop 16 main 2>/dev/null || true
sudo pg_createcluster 16 main
sudo systemctl start postgresql@16-main
sudo systemctl enable postgresql@16-main

# --- ScyllaDB ---
sudo mkdir -p /etc/apt/keyrings
sudo rm -f /etc/apt/sources.list.d/scylladb.list \
           /etc/apt/sources.list.d/scylla.list
sudo gpg --homedir /tmp --no-default-keyring \
  --keyring /etc/apt/keyrings/scylladb.gpg \
  --keyserver hkp://keyserver.ubuntu.com \
  --recv-keys C503C686B007F39E
echo "deb [signed-by=/etc/apt/keyrings/scylladb.gpg] \
  https://downloads.scylladb.com/downloads/scylla/deb/debian-ubuntu/scylladb-2026.1 stable main" \
  | sudo tee /etc/apt/sources.list.d/scylladb.list
sudo apt update
sudo apt install -y scylla
sudo scylla_dev_mode_setup --developer-mode 1
if grep -q "^auto_snapshot" /etc/scylla/scylla.yaml 2>/dev/null; then
  sudo sed -i 's/^auto_snapshot.*/auto_snapshot: false/' /etc/scylla/scylla.yaml
else
  echo "auto_snapshot: false" | sudo tee -a /etc/scylla/scylla.yaml
fi
sudo systemctl enable scylla-server
sudo systemctl start scylla-server
echo "Waiting for ScyllaDB..."
until cqlsh -e "DESCRIBE keyspaces" > /dev/null 2>&1; do sleep 2; done
echo "ScyllaDB ready."
cqlsh << 'CQLEOF'
CREATE KEYSPACE IF NOT EXISTS pixelwise
  WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};
USE pixelwise;
CREATE TABLE IF NOT EXISTS predictions (
  model_version TEXT,
  created_at    TIMESTAMP,
  id            UUID,
  prediction    TEXT,
  confidence    DOUBLE,
  PRIMARY KEY ((model_version), created_at, id)
) WITH CLUSTERING ORDER BY (created_at DESC);
CQLEOF

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

# --- Create PostgreSQL table ---
python3 -c "from app.models import Base, engine; Base.metadata.create_all(engine)"

# --- Nginx + Frontend ---
sudo cp deploy/nginx-pixelwise /etc/nginx/sites-available/pixelwise
sudo ln -sf /etc/nginx/sites-available/pixelwise \
  /etc/nginx/sites-enabled/pixelwise
sudo rm -f /etc/nginx/sites-enabled/default
sudo mkdir -p /var/www/pixelwise
sudo cp -r frontend/* /var/www/pixelwise/
if [ -f .env ]; then
    KEY=$(grep '^SECRET_API_KEY=' .env | cut -d= -f2 | tr -d '\r\n ')
    sudo sed -i "s|^const API_KEY.*|const API_KEY = \"$KEY\";|" \
      /var/www/pixelwise/app.js
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
