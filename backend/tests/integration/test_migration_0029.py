"""0029: templates name their code branch; existing ones keep what they deployed."""

from __future__ import annotations

import psycopg2
import pytest

from c2ai.db.migrate import run_migrations
from c2ai.db.url import psycopg2_connect_kwargs
from tests.integration.conftest import _SERVER_URL
from tests.integration.test_users_db import _database_migrated_to

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")


def test_0029_takes_the_code_branch_and_repository_from_what_ran_before():
    with _database_migrated_to("0028") as url:
        conn = psycopg2.connect(**psycopg2_connect_kwargs(url))
        with conn, conn.cursor() as cursor:
            for number, (code_repository, ref) in enumerate(
                [(None, "release"), ("amberd-ai/code", "main")], start=1
            ):
                cursor.execute(
                    "INSERT INTO registered_applications"
                    " (id, name, application_type, status, current_version, created_by)"
                    " VALUES (%s, %s, 'github_workflow', 'active', 1, 'test')",
                    (f"00000000-0000-4000-8000-00000000000{number}", f"app-{number}"),
                )
                cursor.execute(
                    "INSERT INTO registered_application_versions"
                    " (id, application_id, version, created_by) VALUES (%s, %s, 1, 'test')",
                    (
                        f"00000000-0000-4000-9000-00000000000{number}",
                        f"00000000-0000-4000-8000-00000000000{number}",
                    ),
                )
                cursor.execute(
                    "INSERT INTO registered_application_github_configs"
                    " (application_version_id, github_connection_id, trigger_method,"
                    "  repository, code_repository, workflow_file_path, ref)"
                    " VALUES (%s, 'c', 'workflow_dispatch', 'amberd-ai/devops', %s,"
                    "  '.github/workflows/deploy.yml', %s)",
                    (f"00000000-0000-4000-9000-00000000000{number}", code_repository, ref),
                )
        run_migrations(until="0029")
        with conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT a.name, g.code_repository, g.code_ref, g.repository, g.ref"
                " FROM registered_application_github_configs g"
                " JOIN registered_application_versions v ON v.id = g.application_version_id"
                " JOIN registered_applications a ON a.id = v.application_id"
                " ORDER BY a.name"
            )
            rows = {row[0]: row[1:] for row in cursor.fetchall()}
        conn.close()

    # The code lived in the workflow repository, and deployed from its ref.
    assert rows["app-1"] == ("amberd-ai/devops", "release", "amberd-ai/devops", "release")
    assert rows["app-2"] == ("amberd-ai/code", "main", "amberd-ai/devops", "main")
    assert rows["ADA"] == ("amberd-ai/dealership_new", "main", "amberd-ai/devops", "main")
