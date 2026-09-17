"""Phase 1.13: light concurrency benchmark for the add-measurement path.

Not a performance gate — a smoke test that concurrent writes stay correct
under contention (the atomic helper must serialise them) and complete in
reasonable time. Run with: pytest test/test_load.py -v -s
"""
import time
from concurrent.futures import ThreadPoolExecutor

from database import add_or_replace_measurement, get_all_measurements, init_db

TEST_DB = "test_peakflow.db"
CHILD_ID = 111


def _setup_db():
    import os
    for ext in ["", "-wal", "-shm", "-journal"]:
        p = TEST_DB + ext
        if os.path.exists(p):
            os.remove(p)
    init_db(TEST_DB)


def test_100_concurrent_adds_are_serialised():
    _setup_db()
    workers = 100

    def add(value):
        return add_or_replace_measurement(TEST_DB, 100 + (value % 500), "morning",
                                          CHILD_ID, CHILD_ID)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(add, range(workers)))
    elapsed = time.monotonic() - started

    # Exactly one insert wins the slot (unless forced); the rest report "exists".
    statuses = [status for _, status in results]
    assert statuses.count("ok") == 1
    assert statuses.count("exists") == workers - 1
    assert len(get_all_measurements(TEST_DB, CHILD_ID)) == 1
    assert elapsed < 30, f"contended writes took too long: {elapsed:.1f}s"
