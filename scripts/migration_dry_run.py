"""Dry-run a PeakFlow schema migration on a throwaway copy of the database.

The source database is never written to: it is snapshotted at the filesystem
level (main file plus any ``-wal`` frames), ``init_db`` is run on the copy, and
the resulting schema/data are reported. The source is never opened with SQLite,
so no ``-shm`` is touched and no checkpoint can rewrite it on close. Useful to
preview what ``init_db`` would do before running it against prod.

Usage:
    python -m scripts.migration_dry_run [DB_PATH]
"""
import json
import os
import shutil
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
    """Snapshot ``db_path`` into ``dest_path`` without opening the source.

    A WAL database is fully described by its main file plus any ``-wal`` frames,
    so a filesystem copy captures committed rows. Opening the source with SQLite
    is avoided deliberately: even a read-only connection touches ``-shm``, and a
    read-write connection checkpoints on close, rewriting the main file and
    deleting ``-wal``/``-shm`` beside the source.
    """
    shutil.copyfile(db_path, dest_path)
    wal = db_path + "-wal"
    if os.path.exists(wal):
        shutil.copyfile(wal, dest_path + "-wal")


def dry_run(db_path: str) -> dict:
    """Run ``init_db`` on a copy of ``db_path`` and return a report.

    The source is only read from the filesystem (its main file and ``-wal``), so
    it is never opened by SQLite and cannot be checkpointed or otherwise
    modified. The temp copy and any sidecar files are removed in ``finally``.
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
