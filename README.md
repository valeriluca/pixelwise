# PixelWise — ScyllaDB Branch

This branch ports the PixelWise persistence layer from PostgreSQL 
to ScyllaDB. See the seminar paper for architectural rationale and 
benchmark results.

## Setup (Ubuntu 24.04, 2 GB RAM, 2 vCPU)

```bash
git clone -b scylla https://github.com/valeriluca/pixelwise.git
cd pixelwise
cp .env.example .env
bash setup-server.sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The setup script installs ScyllaDB in developer mode, creates the 
`pixelwise` keyspace and `predictions` table, and pulls the model 
artefact automatically.

## Branch overview

- `main` — PostgreSQL-backed PixelWise (course baseline, Block 6)
- `scylla` — ScyllaDB port (seminar paper artefact)
