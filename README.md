# PixelWise

Handwritten digit classification web application built across the Full Stack Handwerk lecture.

## Branches

- `main` — PostgreSQL persistence, nginx reverse proxy, interactive frontend
- `scylla` — ScyllaDB port for the seminar paper experiment (PostgreSQL + ScyllaDB)

## Setup (fresh Ubuntu 24.04 VM, 2 GB RAM, 2 vCPU)

```bash
git clone -b scylla https://github.com/valeriluca/pixelwise.git
cd pixelwise
cp .env.example .env
# Edit .env: set SECRET_API_KEY, DB_PASSWORD
bash setup-server.sh
```

After setup:
- Frontend: `http://<VM-IP>/`
- API health: `http://<VM-IP>/api/health`
- Benchmark: `python benchmark/benchmark_v2.py postgres --workload both`

## Seminar Paper

*A Comparison of Database Architectures of Real-Time Applications — PostgreSQL versus ScyllaDB in the PixelWise Case*

Supervised by Prof. Dr.-Ing. Mark Schutera, DHBW Ravensburg.
