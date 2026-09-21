"""Dry-run a PeakFlow schema migration on a throwaway copy of the database.

The source database is never written to: it is copied with SQLite's online
backup API (which reads through a hot WAL), ``init_db`` is run on the copy, and
the resulting schema/data are reported. Useful to preview what ``init_db`` would
do before running it against prod.

Usage:
    python -m scripts.migration_dry_run [DB_PATH]
"""
import json
import os
import sqlite3
import sys
import tempfile

from database import SCHEMA_VERSION, init_db

_ORPHAN_SQL = (
    "SELECT DISTINCT child_id FROM measurements "
    "WHERE child_id NOT IN (SELECT telegram_id FROM members) "
    "ORDER BY child_id"
)


def _user_version(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _table_counts(conn: sqlite3.Connection) -> dict:
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {
        name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in names
    }


def _backup_to(db_path: str, dest_path: str) -> None:
    """SQLite online backup (WAL-safe), mirroring ``database.backup_db``."""
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def dry_run(db_path: str) -> dict:
    """Run ``init_db`` on a copy of ``db_path`` and return a report.

    The source is only ever read (via the online backup API). ``version_before``
    is read from the copy, so the source is never opened read-only (which would
    create ``-shm``/``-wal`` sidecars beside it). The temp copy and any sidecar
    files are removed in ``finally``.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"database not found: {db_path}")
    fd, tmp_path = tempfile.mkstemp(prefix="peakflow_dryrun_", suffix=".db")
    os.close(fd)
    try:
        _backup_to(db_path, tmp_path)
        version_before = _user_version(tmp_path)
        init_db(tmp_path)
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        try:
            version_after = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = _table_counts(conn)
            families = [dict(r) for r in conn.execute(
                "SELECT * FROM families ORDER BY id"
            )]
            members = [dict(r) for r in conn.execute(
                "SELECT * FROM members ORDER BY telegram_id"
            )]
            orphan_child_ids = [r[0] for r in conn.execute(_ORPHAN_SQL)]
        finally:
            conn.close()
        return {
            "source": db_path,
            "version_before": version_before,
            "version_after": version_after,
            "schema_version": SCHEMA_VERSION,
            "tables": tables,
            "families": families,
            "members": members,
            "orphan_child_ids": orphan_child_ids,
        }
    finally:
        for suffix in ("", ".v1.bak", "-wal", "-shm", "-journal"):
            sidecar = tmp_path + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)


def _main(argv: list) -> int:
    if len(argv) > 1:
        db_path = argv[1]
    else:
        import config
        db_path = os.environ.get("DB_PATH") or getattr(config, "DB_PATH", "peakflow.db")
    report = dry_run(db_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
