# PixelWise Database Benchmark

Companion experiment for the seminar paper
"A Comparison of Database Architectures of Real-Time Applications".

## Workloads
- W1: Sequential single-writer, 10,000 rows + three read patterns
- W2: 8 concurrent writers, 1,250 rows each

## Usage
    python benchmark/benchmark_v2.py postgres --workload w1
    python benchmark/benchmark_v2.py postgres --workload w2
    python benchmark/benchmark_v2.py scylla   --workload w1
    python benchmark/benchmark_v2.py scylla   --workload w2

## Requirements
DATABASE_URL, SCYLLA_HOST, SCYLLA_KEYSPACE, MODEL_VERSION in .env
Packages: psycopg2-binary, cassandra-driver, scikit-learn, numpy, python-dotenv

## Output
results_{backend}_{workload}.json with mean, P50, P95, P99 in milliseconds.
