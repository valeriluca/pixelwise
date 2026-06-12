#!/usr/bin/env python3
# PixelWise persistence benchmark
# Compares PostgreSQL and ScyllaDB under two workloads:
#   W1: sequential single-writer, 10,000 rows + three read patterns
#   W2: 8 concurrent writers, 1,250 rows each
# Usage:
#   python benchmark/benchmark_v2.py postgres --workload w1
#   python benchmark/benchmark_v2.py scylla   --workload w2 --workers 8

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import time
import uuid
import statistics
import json
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────
N_ROWS      = 10_000   # rows per W1 write phase / W2 total
N_RUNS      = 20       # timed runs after warm-up
N_WARMUP    = 3        # warm-up runs (discarded)
READ_LIMIT  = 20       # rows fetched in each read pattern
OFFSET_MID  = 5_000    # offset for R3 (midpoint of N_ROWS)
W2_WORKERS  = 8        # default concurrent writers for W2

# ── MNIST-derived row payloads ───────────────────────────────────────────────
_mnist_predictions_cache = None

def get_realistic_predictions(n_needed):
    """Return n_needed prediction dicts from MNIST samples run through the
    v1 classifier. First call loads MNIST and runs inference; subsequent
    calls return cached results."""
    global _mnist_predictions_cache
    if n_needed > 70_000:
        raise ValueError(
            f"Requested {n_needed} samples, MNIST has only 70,000.")
    if (_mnist_predictions_cache is None
            or len(_mnist_predictions_cache) < n_needed):
        from sklearn.datasets import fetch_openml
        from app.classifier import classify_batch
        print(f"  Loading MNIST + classifying {n_needed} samples "
              "(one-time setup)...", flush=True)
        X, _ = fetch_openml("mnist_784", version=1,
                            return_X_y=True, as_frame=False,
                            parser="liac-arff")
        images = X[:n_needed].reshape(-1, 28, 28).astype(np.uint8)
        _mnist_predictions_cache = classify_batch(images)
        print(f"  Cached {len(_mnist_predictions_cache)} predictions",
              flush=True)
    return _mnist_predictions_cache[:n_needed]

def make_rows(backend, n):
    """Build n row tuples for the given backend. PostgreSQL uses a SERIAL
    primary key; ScyllaDB requires a client-generated UUID."""
    now = datetime.now(timezone.utc)
    preds = get_realistic_predictions(n)
    if backend == "postgres":
        return [(p["prediction"], p["confidence"], "v1", now) for p in preds]
    return [(uuid.uuid4(), p["prediction"], p["confidence"], "v1", now)
            for p in preds]

# ── Statistics ───────────────────────────────────────────────────────────────
def percentile(data, p):
    """Linear-interpolated percentile of a numeric sample."""
    s = sorted(data)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] + (s[c] - s[f]) * (k - f)

def report(label, durations_s):
    """Print and return mean/P50/P95/P99 in milliseconds."""
    ms = [d * 1000 for d in durations_s]
    stats = {
        "mean": round(statistics.mean(ms), 2),
        "p50":  round(percentile(ms, 50), 2),
        "p95":  round(percentile(ms, 95), 2),
        "p99":  round(percentile(ms, 99), 2),
    }
    print(f"\n  {label}")
    print(f"  mean={stats['mean']}ms  P50={stats['p50']}ms  "
          f"P95={stats['p95']}ms  P99={stats['p99']}ms")
    return stats

# ── PostgreSQL driver ────────────────────────────────────────────────────────
def pg_connect():
    """Open a PostgreSQL connection from DATABASE_URL."""
    import psycopg2
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def pg_truncate(conn):
    """Empty the predictions table and reset the SERIAL sequence."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE predictions RESTART IDENTITY;")
    conn.commit()

def pg_insert_batch(conn, rows):
    """Batch-insert rows via executemany, one commit per batch."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO predictions "
            "(prediction, confidence, model_version, created_at) "
            "VALUES (%s, %s, %s, %s)",
            rows,
        )
    conn.commit()

def pg_read_r1(conn):
    """R1: newest 20 rows by created_at DESC."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, prediction, confidence, created_at "
            "FROM predictions ORDER BY created_at DESC LIMIT %s",
            (READ_LIMIT,),
        )
        return cur.fetchall()

def pg_read_r2(conn):
    """R2: oldest 20 rows by created_at ASC."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, prediction, confidence, created_at "
            "FROM predictions ORDER BY created_at ASC LIMIT %s",
            (READ_LIMIT,),
        )
        return cur.fetchall()

def pg_read_r3(conn):
    """R3: 20 rows from the middle of the table via OFFSET."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, prediction, confidence, created_at "
            "FROM predictions ORDER BY created_at ASC "
            "LIMIT %s OFFSET %s",
            (READ_LIMIT, OFFSET_MID),
        )
        return cur.fetchall()

# ── ScyllaDB driver ──────────────────────────────────────────────────────────
def scylla_connect():
    """Open a ScyllaDB session. Returns cluster handle and session."""
    from cassandra.cluster import Cluster
    from cassandra.policies import DCAwareRoundRobinPolicy
    cluster = Cluster(
        [os.getenv("SCYLLA_HOST", "127.0.0.1")],
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
        connect_timeout=30,
    )
    session = cluster.connect(os.getenv("SCYLLA_KEYSPACE", "pixelwise"))
    session.default_timeout = 180
    return cluster, session

def scylla_truncate(session):
    """Empty the predictions table."""
    session.execute("TRUNCATE predictions;")

def scylla_insert_batch(session, rows, prepared):
    """Insert rows sequentially via prepared statement. Sequential by
    design to match the synchronous PostgreSQL client pattern."""
    for row in rows:
        session.execute(prepared, row)

def scylla_read_r1(session):
    """R1: newest 20 rows, partition-local DESC scan."""
    return list(session.execute(
        "SELECT id, prediction, confidence, created_at "
        "FROM predictions WHERE model_version = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (os.getenv("MODEL_VERSION", "v1"), READ_LIMIT),
    ))

def scylla_read_r2(session):
    """R2: oldest 20 rows, partition-local ASC scan."""
    return list(session.execute(
        "SELECT id, prediction, confidence, created_at "
        "FROM predictions WHERE model_version = %s "
        "ORDER BY created_at ASC LIMIT %s",
        (os.getenv("MODEL_VERSION", "v1"), READ_LIMIT),
    ))

# R3 has no ScyllaDB equivalent: CQL has no OFFSET operator.
# Mid-range access requires token-based cursor paging, a different
# query model rather than a slower version of the same operation.

# ── Workload W1: sequential single-writer ────────────────────────────────────
def run_w1(backend, conn_or_session, prepared=None):
    """Run 3 warm-up + 20 timed iterations: truncate, insert, read x3."""
    write_t, r1_t, r2_t, r3_t = [], [], [], []

    for run in range(N_WARMUP + N_RUNS):
        rows  = make_rows(backend, N_ROWS)
        label = "WARMUP" if run < N_WARMUP else f"RUN {run-N_WARMUP+1:02d}"
        print(f"  {label}", end=" ", flush=True)

        if backend == "postgres":
            pg_truncate(conn_or_session)
        else:
            scylla_truncate(conn_or_session)

        t0 = time.perf_counter()
        if backend == "postgres":
            pg_insert_batch(conn_or_session, rows)
        else:
            scylla_insert_batch(conn_or_session, rows, prepared)
        w = time.perf_counter() - t0

        t0 = time.perf_counter()
        if backend == "postgres":
            pg_read_r1(conn_or_session)
        else:
            scylla_read_r1(conn_or_session)
        r1 = time.perf_counter() - t0

        t0 = time.perf_counter()
        if backend == "postgres":
            pg_read_r2(conn_or_session)
        else:
            scylla_read_r2(conn_or_session)
        r2 = time.perf_counter() - t0

        r3 = None
        if backend == "postgres":
            t0 = time.perf_counter()
            pg_read_r3(conn_or_session)
            r3 = time.perf_counter() - t0

        print(f"write={w*1000:.0f}ms R1={r1*1000:.0f}ms "
              f"R2={r2*1000:.0f}ms "
              + (f"R3={r3*1000:.0f}ms" if r3 else "R3=n/a"))

        if run >= N_WARMUP:
            write_t.append(w)
            r1_t.append(r1)
            r2_t.append(r2)
            if r3 is not None:
                r3_t.append(r3)

    results = {
        "write": report(f"W1 WRITE -- {backend}", write_t),
        "r1":    report(f"W1 R1 (newest 20) -- {backend}", r1_t),
        "r2":    report(f"W1 R2 (oldest 20) -- {backend}", r2_t),
    }
    if r3_t:
        results["r3"] = report("W1 R3 (middle 20 via OFFSET) -- postgres",
                               r3_t)
    else:
        results["r3"] = "not_applicable"
        print("\n  W1 R3 -- ScyllaDB: not applicable (CQL has no OFFSET)")
    return results

# ── Workload W2: concurrent writers ──────────────────────────────────────────
def _worker_pg(db_url, n_rows):
    """W2 worker: fresh connection, insert n_rows, close."""
    import psycopg2
    conn = psycopg2.connect(db_url)
    rows = make_rows("postgres", n_rows)
    t0 = time.perf_counter()
    pg_insert_batch(conn, rows)
    conn.close()
    return time.perf_counter() - t0

def _worker_scylla(host, keyspace, n_rows):
    """W2 worker: fresh session, insert n_rows, shutdown."""
    from cassandra.cluster import Cluster
    from cassandra.policies import DCAwareRoundRobinPolicy
    cluster = Cluster(
        [host],
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
        connect_timeout=30,
    )
    session = cluster.connect(keyspace)
    prepared = session.prepare(
        "INSERT INTO predictions "
        "(id, prediction, confidence, model_version, created_at) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    rows = make_rows("scylla", n_rows)
    t0 = time.perf_counter()
    scylla_insert_batch(session, rows, prepared)
    cluster.shutdown()
    return time.perf_counter() - t0

def run_w2(backend, n_workers):
    """Run 3 warm-up + 20 timed iterations of concurrent inserts."""
    rows_per_worker = N_ROWS // n_workers
    wall_t = []

    get_realistic_predictions(rows_per_worker)

    for run in range(N_WARMUP + N_RUNS):
        label = "WARMUP" if run < N_WARMUP else f"RUN {run-N_WARMUP+1:02d}"
        print(f"  {label} {n_workers}x{rows_per_worker} rows",
              end=" ", flush=True)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            if backend == "postgres":
                futs = [pool.submit(_worker_pg,
                                    os.getenv("DATABASE_URL"),
                                    rows_per_worker)
                        for _ in range(n_workers)]
            else:
                futs = [pool.submit(_worker_scylla,
                                    os.getenv("SCYLLA_HOST", "127.0.0.1"),
                                    os.getenv("SCYLLA_KEYSPACE", "pixelwise"),
                                    rows_per_worker)
                        for _ in range(n_workers)]
            for f in as_completed(futs):
                f.result()
        wall = time.perf_counter() - t0
        print(f"wall={wall*1000:.0f}ms")

        if run >= N_WARMUP:
            wall_t.append(wall)

    return {"concurrent_write": report(
        f"W2 CONCURRENT WRITE -- {backend} ({n_workers} workers)", wall_t)}

# ── Main entry point ─────────────────────────────────────────────────────────
def main():
    """Parse arguments, warm the MNIST cache, run the selected workload."""
    parser = argparse.ArgumentParser(
        description="Compare PostgreSQL and ScyllaDB on the PixelWise "
                    "persistence layer.")
    parser.add_argument("backend", choices=["postgres", "scylla"])
    parser.add_argument("--workload", choices=["w1", "w2", "both"],
                        default="w1")
    parser.add_argument("--workers", type=int, default=W2_WORKERS,
                        help="Concurrent writers for W2 (default: 8)")
    args = parser.parse_args()

    print(f"\n{'='*50}")
    print(f"  backend={args.backend}  workload={args.workload}")
    print(f"{'='*50}\n")

    get_realistic_predictions(N_ROWS)

    results = {}

    if args.backend == "postgres":
        conn = pg_connect()
        if args.workload in ("w1", "both"):
            results["w1"] = run_w1("postgres", conn)
        if args.workload in ("w2", "both"):
            conn.close()
            results["w2"] = run_w2("postgres", args.workers)
        else:
            conn.close()
    else:
        cluster, session = scylla_connect()
        prepared = session.prepare(
            "INSERT INTO predictions "
            "(id, prediction, confidence, model_version, created_at) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        if args.workload in ("w1", "both"):
            results["w1"] = run_w1("scylla", session, prepared)
        if args.workload in ("w2", "both"):
            cluster.shutdown()
            results["w2"] = run_w2("scylla", args.workers)
        else:
            cluster.shutdown()

    out = f"results_{args.backend}_{args.workload}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  -> {out}\n")

if __name__ == "__main__":
    main()
