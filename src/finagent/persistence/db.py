"""SQLite access: explicit transactions and versioned migrations."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
MIGRATION_KINDS = frozenset({"sandbox", "app"})


def connect(path: Path | str) -> sqlite3.Connection:
    # isolation_level=None turns off implicit transactions, so transaction() alone controls them.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    # Below the 3.0 s write deadline, so a write cannot commit after reconciliation found no row.
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection, kind: str) -> None:
    """Run every migration of this kind in name order. It runs outside transaction()."""
    if kind not in MIGRATION_KINDS:
        raise ValueError(f"unknown migration kind: {kind}")
    for path in sorted((MIGRATIONS / kind).glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
