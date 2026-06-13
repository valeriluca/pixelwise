# PixelWise

Handwritten digit classification web application built across the Full Stack Handwerk lecture.

## Branches

- `main` — PostgreSQL persistence, nginx reverse proxy, interactive frontend
- `scylla` — ScyllaDB port for the seminar paper (PostgreSQL + ScyllaDB)

## Setup (fresh Ubuntu 24.04 VM, 2 GB RAM, 2 vCPU)

```bash
git clone https://github.com/valeriluca/pixelwise.git
cd pixelwise
cp .env.example .env
nano .env   # set SECRET_API_KEY and DB_PASSWORD
bash setup-server.sh
```

The setup script installs PostgreSQL, nginx, creates the database and table, pulls the model artefact, deploys the frontend, and starts the service via systemd.

After setup:
- Frontend: http://<VM-IP>/ (from host browser) or http://localhost/ (from VM)
- API health: http://<VM-IP>/api/health
- Recent predictions: http://<VM-IP>/api/results
