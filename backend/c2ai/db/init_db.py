"""Development bootstrap: recreate the database, migrate, and seed an admin.

Usage (from backend/):
    python -m c2ai.db.init_db --yes

This DROPS the database named in DATABASE_URL. It is meant for local
development only; use ``python -m c2ai.db.migrate`` everywhere else.

The seeded administrator is ``admin`` with the password from
``C2AI_ADMIN_PASSWORD`` (default ``admin``) and must change it on first login.
"""

from __future__ import annotations

import argparse
import logging
import sys

import psycopg2
from argon2 import PasswordHasher
from psycopg2 import sql
from psycopg2.extras import Json

from c2ai.config import get_settings
from c2ai.db.migrate import run_migrations
from c2ai.db.url import psycopg2_connect_kwargs, sync_database_url

logger = logging.getLogger(__name__)


def recreate_database() -> None:
    """Drop and recreate the target database via the ``postgres`` maintenance DB."""

    url = sync_database_url()
    if not url.database:
        raise RuntimeError("DATABASE_URL must name a database.")
    conn = psycopg2.connect(**psycopg2_connect_kwargs(url, database="postgres"))
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (url.database,),
            )
            name = sql.Identifier(url.database)
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(name))
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(name))
        logger.info("Database '%s' recreated.", url.database)
    finally:
        conn.close()


def seed_admin_user() -> None:
    """Insert the bootstrap administrator (no-op when it already exists)."""

    password = get_settings().admin_password
    conn = psycopg2.connect(**psycopg2_connect_kwargs(sync_database_url()))
    try:
        with conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users
                    (identifier, password, first_name, last_name, metadata, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (identifier) DO NOTHING
                """,
                (
                    "admin",
                    PasswordHasher().hash(password),
                    "Super",
                    "Admin",
                    Json(
                        {
                            "user_type": "Admin",
                            "role": "Executive",
                            "needs_password_reset": True,
                        }
                    ),
                    "system",
                ),
            )
        logger.info("Admin user seeded.")
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="Confirm dropping the database.")
    args = parser.parse_args()
    if not args.yes:
        sys.exit(
            f"Refusing to drop '{sync_database_url().database}' without --yes. "
            "Use 'python -m c2ai.db.migrate' to upgrade an existing database."
        )
    recreate_database()
    run_migrations()
    seed_admin_user()
    logger.info("Database setup completed.")


if __name__ == "__main__":
    main()
