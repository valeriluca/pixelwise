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
nano .env   # set SECRET_API_KEY
bash setup-server.sh
```

The setup script installs PostgreSQL, ScyllaDB (developer mode), nginx, creates all databases and tables, pulls the model artefact, deploys the frontend, and starts the service via systemd.

## Benchmarking

### Full benchmark (both W1 and W2):
```bash
source .venv/bin/activate 
sudo systemctl stop postgresql@16-main
python benchmark/benchmark_v2.py scylla --workload both
sudo systemctl start postgresql@16-main
sudo systemctl stop scylla-server
python benchmark/benchmark_v2.py postgres --workload both
```

### Individual workloads:

**W1** (sequential single-writer, 10,000 rows + three read patterns):
```bash
source .venv/bin/activate
python benchmark/benchmark_v2.py postgres --workload w1
python benchmark/benchmark_v2.py scylla --workload w1
```

**W2** (8 concurrent writers, 1,250 rows each):
```bash
source .venv/bin/activate
python benchmark/benchmark_v2.py postgres --workload w2
python benchmark/benchmark_v2.py scylla --workload w2
```

Results are saved as `results_{backend}_{workload}.json` with latency metrics (mean, P50, P95, P99) in milliseconds.

## Seminar Paper

*A Comparison of Database Architectures for Real-Time Applications — PostgreSQL versus ScyllaDB in the PixelWise Case*

Supervised by Prof. Dr.-Ing. Mark Schutera, DHBW Ravensburg.
