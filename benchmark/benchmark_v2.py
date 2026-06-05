#!/usr/bin/env python3
"""

# Repository root must be on sys.path so 'from app.classifier ...' resolves
# regardless of the working directory the script is invoked from.
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
    """Return n_needed prediction dicts produced by running MNIST samples
    through the v1 classifier. The first call loads MNIST and performs the
    inference; subsequent calls return cached results.

    The cache replaces homogeneous placeholder payloads ('5', 0.92) with
    realistic value distributions across the row set, so that compression
    behaviour, partition layout, and any value-sensitive driver overhead
    reflect the live system rather than artefacts of constant input.
    """
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
    """Build a list of n row tuples for the given backend. The tuple shape
    matches the parameterised INSERT statement of each driver: PostgreSQL
    relies on a SERIAL primary key, ScyllaDB requires a client-generated
    UUID as part of the composite key."""
    now = datetime.now(timezone.utc)
    preds = get_realistic_predictions(n)
    if backend == "postgres":
        return [(p["prediction"], p["confidence"], "v1", now) for p in preds]
    return [(uuid.uuid4(), p["prediction"], p["confidence"], "v1", now)
            for p in preds]

# ── Statistics ───────────────────────────────────────────────────────────────
def percentile(data, p):
    """Linear-interpolated percentile of a numeric sample, matching the
    'inclusive' definition used by most percentile reporting tools."""
    s = sorted(data)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] + (s[c] - s[f]) * (k - f)

def report(label, durations_s):
    """Print and return summary statistics for a list of durations in
    seconds. Output values are in milliseconds, rounded to two decimals."""
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
    """Open a PostgreSQL connection from the DATABASE_URL environment
    variable. The connection is reused across all iterations of one run."""
    import psycopg2
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def pg_truncate(conn):
    """Empty the predictions table and reset the SERIAL sequence."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE predictions RESTART IDENTITY;")
    conn.commit()

def pg_insert_batch(conn, rows):
    """Batch-insert the row list via executemany. One commit per batch."""
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
    """Open a ScyllaDB session against the configured keyspace. Returns
    both the cluster handle (for shutdown) and the session."""
    from cassandra.cluster import Cluster
    from cassandra.policies import DCAwareRoundRobinPolicy
    cluster = Cluster(
        [os.getenv("SCYLLA_HOST", "127.0.0.1")],
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
    )
    session = cluster.connect(os.getenv("SCYLLA_KEYSPACE", "pixelwise"))
    return cluster, session

def scylla_truncate(session):
    """Empty the predictions table."""
    session.execute("TRUNCATE predictions;")

def scylla_insert_batch(session, rows, prepared):
    """Insert rows sequentially via a prepared statement. Sequential rather
    than async by design — see the module docstring."""
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

# R3 has no ScyllaDB counterpart: CQL lacks OFFSET, and mid-range access
# in a wide-column model requires token-based cursor paging, which is a
# different query semantics rather than a slower implementation of the
# same query.

# ── Workload W1: sequential single-writer ────────────────────────────────────
def run_w1(backend, conn_or_session, prepared=None):
    """Drive 23 iterations (3 warm-up + 20 timed) of the W1 workload:
    truncate, insert N_ROWS rows, then run three read patterns."""
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
        "write": report(f"W1 WRITE — {backend}", write_t),
        "r1":    report(f"W1 R1 (newest 20) — {backend}", r1_t),
        "r2":    report(f"W1 R2 (oldest 20) — {backend}", r2_t),
    }
    if r3_t:
        results["r3"] = report("W1 R3 (middle 20 via OFFSET) — postgres",
                               r3_t)
    else:
        results["r3"] = "not_applicable"
        print("\n  W1 R3 — ScyllaDB: not applicable (CQL has no OFFSET)")
    return results

# ── Workload W2: concurrent writers ──────────────────────────────────────────
def _worker_pg(db_url, n_rows):
    """W2 worker: open a fresh PostgreSQL connection, insert n_rows, close.
    A separate connection per worker is required because a single
    connection cannot serve concurrent statements."""
    import psycopg2
    conn = psycopg2.connect(db_url)
    rows = make_rows("postgres", n_rows)
    t0 = time.perf_counter()
    pg_insert_batch(conn, rows)
    conn.close()
    return time.perf_counter() - t0

def _worker_scylla(host, keyspace, n_rows):
    """W2 worker: open a fresh ScyllaDB session, insert n_rows, shut down.
    The cassandra-driver session is technically thread-safe, but a per-worker
    session matches the per-connection PostgreSQL setup."""
    from cassandra.cluster import Cluster
    from cassandra.policies import DCAwareRoundRobinPolicy
    cluster = Cluster(
        [host],
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="datacenter1"),
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
    """Drive 23 iterations of the W2 workload: spawn n_workers threads,
    each inserting N_ROWS/n_workers rows, and time the wall-clock duration
    until all workers complete."""
    rows_per_worker = N_ROWS // n_workers
    wall_t = []

    # Preload the MNIST cache so the first warm-up iteration is not
    # inflated by the one-time inference cost.
    get_realistic_predictions(rows_per_worker)

    for run in range(N_WARMUP + N_RUNS):
        label = "WARMUP" if run < N_WARMUP else f"RUN {run-N_WARMUP+1:02d}"
        print(f"  {label} {n_workers}×{rows_per_worker} rows",
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
        f"W2 CONCURRENT WRITE — {backend} ({n_workers} workers)", wall_t)}

# ── Main entry point ─────────────────────────────────────────────────────────
def main():
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

    # Warm the MNIST cache before any timing starts.
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
    print(f"\n  → {out}\n")

if __name__ == "__main__":
    main()
