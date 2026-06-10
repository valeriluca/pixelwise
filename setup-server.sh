#!/bin/bash
set -euo pipefail

sudo apt update
sudo apt install -y git python3 python3-pip python3-venv curl

# Install ScyllaDB
sudo mkdir -p /etc/apt/keyrings
sudo curl -o /etc/apt/keyrings/scylladb.gpg \
  https://downloads.scylladb.com/deb/debian/scylladb-2026.1/scylladb.gpg
echo "deb [signed-by=/etc/apt/keyrings/scylladb.gpg] \
  https://downloads.scylladb.com/deb/debian scylladb-2026.1 main" \
  | sudo tee /etc/apt/sources.list.d/scylladb.list
sudo apt update
sudo apt install -y scylla

# Dev mode + disable auto_snapshot
sudo scylla_dev_mode_setup --developer-mode 1
grep -q "^auto_snapshot" /etc/scylla/scylla.yaml && \
  sudo sed -i 's/^auto_snapshot.*/auto_snapshot: false/' \
    /etc/scylla/scylla.yaml || \
  echo "auto_snapshot: false" | \
    sudo tee -a /etc/scylla/scylla.yaml

sudo systemctl enable scylla-server
sudo systemctl start scylla-server

# Wait for ScyllaDB
echo "Waiting for ScyllaDB to be ready..."
until cqlsh -e "DESCRIBE keyspaces" > /dev/null 2>&1; do
  sleep 2
done
echo "ScyllaDB ready."

# Create keyspace and table
cqlsh -e "
CREATE KEYSPACE IF NOT EXISTS pixelwise
  WITH replication = {
    'class': 'SimpleStrategy',
    'replication_factor': 1
  };
USE pixelwise;
CREATE TABLE IF NOT EXISTS predictions (
  model_version TEXT,
  created_at    TIMESTAMP,
  id            UUID,
  prediction    TEXT,
  confidence    DOUBLE,
  PRIMARY KEY ((model_version), created_at, id)
) WITH CLUSTERING ORDER BY (created_at DESC);
"

# Pull model artefact
if [ -f .env ]; then
    set -a; source .env; set +a
    if [ -n "${MODEL_REPO:-}" ] && \
       [ -n "${MODEL_VERSION:-}" ]; then
        mkdir -p models/
        rm -rf /tmp/pixelwise-model
        git clone --depth 1 --branch "$MODEL_VERSION" \
            "$MODEL_REPO" /tmp/pixelwise-model
        cp /tmp/pixelwise-model/*.pkl models/
        cp /tmp/pixelwise-model/MODELCARD.md models/
        rm -rf /tmp/pixelwise-model
    fi
fi

# Install systemd service on prod
if [ -f deploy/pixelwise.service ] && \
   command -v systemctl >/dev/null 2>&1 && \
   id produser >/dev/null 2>&1; then
    sudo cp deploy/pixelwise.service \
      /etc/systemd/system/pixelwise.service
    sudo systemctl daemon-reload
    sudo systemctl enable pixelwise
    sudo systemctl restart pixelwise
    sudo systemctl status pixelwise --no-pager
fi
