"""Forward-only SQL migration runner.

Usage (from backend/):
    python -m c2ai.db.migrate            # apply all pending migrations
    python -m c2ai.db.migrate --stamp    # record pending migrations as applied
                                         # without running them (one-off, for
                                         # databases that already match head)

Each ``migrations/NNNN_name.sql`` file runs in its own transaction together
with the insert into ``schema_migrations``; a failure rolls back that file and
stops the run. Applied files must never be edited — add a new file instead.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import psycopg2

from c2ai.db.url import psycopg2_connect_kwargs, sync_database_url

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_ENSURE_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT        PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def pending_migrations(applied: set[str]) -> list[Path]:
    """Migration files not yet recorded, in lexical (= numeric) order."""

    return [path for path in sorted(MIGRATIONS_DIR.glob("*.sql")) if path.stem not in applied]


def run_migrations(stamp: bool = False, *, until: str | None = None) -> int:
    """Apply (or stamp) pending migrations; return how many were processed.

    ``until`` stops after the migration whose name starts with it (e.g.
    ``"0020"``), which lets tests seed data in an older schema.
    """

    conn = psycopg2.connect(**psycopg2_connect_kwargs(sync_database_url()))
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(_ENSURE_VERSION_TABLE)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        pending = pending_migrations(applied)
        if until is not None:
            pending = [path for path in pending if path.stem[: len(until)] <= until]
        if not pending:
            logger.info("All migrations already applied.")
            return 0

        for migration in pending:
            try:
                with conn.cursor() as cur:
                    if not stamp:
                        cur.execute(migration.read_text())
                    cur.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)",
                        (migration.stem,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                logger.error("Migration %s failed; it was rolled back.", migration.stem)
                raise
            logger.info("  %s ... %s", migration.stem, "stamped" if stamp else "applied")
        return len(pending)
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="Record pending migrations as applied without executing them.",
    )
    args = parser.parse_args()
    count = run_migrations(stamp=args.stamp)
    logger.info("%s migration(s) %s.", count, "stamped" if args.stamp else "applied")


if __name__ == "__main__":
    main()
