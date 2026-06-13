# PixelWise

Handwritten digit classification web application built across the Full Stack Handwerk lecture.

## Branches

- `main` — PostgreSQL persistence, nginx reverse proxy, interactive frontend
- `scylla` — ScyllaDB port for the seminar paper (PostgreSQL + ScyllaDB)

## Setup (fresh Ubuntu 24.04 VM, 2 GB RAM, 2 vCPU)

```bash
git clone -b scylla https://github.com/valeriluca/pixelwise.git
cd pixelwise
cp .env.example .env
nano .env   # set SECRET_API_KEY and DB_PASSWORD
bash setup-server.sh
```

The setup script installs PostgreSQL, ScyllaDB (developer mode), nginx, creates all databases and tables, pulls the model artefact, deploys the frontend, and starts the service via systemd.

After setup:
- Frontend: http://<VM-IP>/
- API health: http://<VM-IP>/api/health
- Benchmark: `source .venv/bin/activate && python benchmark/benchmark_v2.py scylla --workload both`

## Seminar Paper

*A Comparison of Database Architectures for Real-Time Applications — PostgreSQL versus ScyllaDB in the PixelWise Case*

Supervised by Prof. Dr.-Ing. Mark Schutera, DHBW Ravensburg.
