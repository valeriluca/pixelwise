import time
import sys
import psycopg2
from cassandra.cluster import Cluster
import uuid
import datetime

WARMUP = 3   # Warm-up Runs, werden nicht gemessen
RUNS = 20    # gemessene Runs

def benchmark_postgres():
    # Verbindung zur PostgreSQL Datenbank
    conn = psycopg2.connect(
        host="localhost",
        database="pixelwise",
        user="pixelwise",
        password="secret"
    )
    cur = conn.cursor()

    write_times = []
    read_times = []

    for run in range(WARMUP + RUNS):

        # Tabelle leeren vor jedem Run
        cur.execute("DELETE FROM predictions")
        conn.commit()

        # Write-Phase: 10.000 Zeilen einfügen
        start = time.time()
        for i in range(10000):
            cur.execute(
                "INSERT INTO predictions (prediction, confidence, model_version) VALUES (%s, %s, %s)",
                (str(i % 9 + 1), 0.95, "v1")
            )
        conn.commit()
        write_time = time.time() - start

        # Read-Phase: letzte 20 Zeilen abrufen
        start = time.time()
        cur.execute("SELECT * FROM predictions ORDER BY created_at DESC LIMIT 20")
        cur.fetchall()
        read_time = time.time() - start

        # Warm-up Runs ignorieren
        if run < WARMUP:
            print(f"Warmup {run+1}/{WARMUP} done")
            continue

        write_times.append(write_time)
        read_times.append(read_time)
        print(f"Run {run - WARMUP + 1}/{RUNS} done")

    cur.close()
    conn.close()

    # Ergebnisse ausgeben
    print(f"\n--- PostgreSQL Ergebnisse ({RUNS} Runs, {WARMUP} Warmup) ---")
    print(f"Write Durchschnitt: {sum(write_times)/RUNS:.4f}s")
    print(f"Write Min:          {min(write_times):.4f}s")
    print(f"Write Max:          {max(write_times):.4f}s")
    print(f"Read  Durchschnitt: {sum(read_times)/RUNS:.4f}s")
    print(f"Read  Min:          {min(read_times):.4f}s")
    print(f"Read  Max:          {max(read_times):.4f}s")


def benchmark_scylla():
    # Verbindung zur ScyllaDB Datenbank
    cluster = Cluster(['localhost'])
    session = cluster.connect('pixelwise')

    write_times = []
    read_times = []

    for run in range(WARMUP + RUNS):

        # Tabelle leeren vor jedem Run
        session.execute("TRUNCATE predictions")

        # Write-Phase: 10.000 Zeilen einfügen
        start = time.time()
        for i in range(10000):
            session.execute(
                "INSERT INTO predictions (model_version, created_at, id, prediction, confidence) VALUES (%s, %s, %s, %s, %s)",
                ("v1", datetime.datetime.utcnow(), uuid.uuid4(), str(i % 9 + 1), 0.95)
            )
        write_time = time.time() - start

        # Read-Phase: letzte 20 Zeilen abrufen
        start = time.time()
        session.execute("SELECT * FROM predictions WHERE model_version='v1' LIMIT 20")
        read_time = time.time() - start

        # Warm-up Runs ignorieren
        if run < WARMUP:
            print(f"Warmup {run+1}/{WARMUP} done")
            continue

        write_times.append(write_time)
        read_times.append(read_time)
        print(f"Run {run - WARMUP + 1}/{RUNS} done")

    cluster.shutdown()

    # Ergebnisse ausgeben
    print(f"\n--- ScyllaDB Ergebnisse ({RUNS} Runs, {WARMUP} Warmup) ---")
    print(f"Write Durchschnitt: {sum(write_times)/RUNS:.4f}s")
    print(f"Write Min:          {min(write_times):.4f}s")
    print(f"Write Max:          {max(write_times):.4f}s")
    print(f"Read  Durchschnitt: {sum(read_times)/RUNS:.4f}s")
    print(f"Read  Min:          {min(read_times):.4f}s")
    print(f"Read  Max:          {max(read_times):.4f}s")


if __name__ == "__main__":
    if sys.argv[1] == "postgres":
        benchmark_postgres()
    elif sys.argv[1] == "scylla":
        benchmark_scylla()
