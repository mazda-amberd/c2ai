"""Migration 0023 folds the legacy ADA history into the unified deployment model."""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from c2ai.config import get_settings
from c2ai.db.migrate import run_migrations
from c2ai.db.url import psycopg2_connect_kwargs
from tests.integration.conftest import _SERVER_URL, _admin_connection

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

ADA = "ada00000-0000-4000-8000-000000000001"

LEGACY = """
INSERT INTO deployments (subdomain, customer_name, env_instance, tier, branch, domain, created_at)
VALUES ('amberd-acme-ada', 'acme', 'ada', 1, 'v1', 'amberd.ai', now() - interval '3 days');

INSERT INTO pipeline_runs (id, subdomain, operation, event_type, triggered_by, run_id, tier,
                           branch, dispatched_at, ended_at) VALUES
 ('acme-1', 'amberd-acme-ada', 'deploy', 'ada-deploy.yaml', 'alice', 11, 1, 'v1',
  now() - interval '3 days', now() - interval '3 days'),
 ('acme-2', 'amberd-acme-ada', 'update', 'ada-update.yaml', 'bob', 12, 1, 'v2',
  now() - interval '1 day', now() - interval '1 day'),
 ('beta-old', 'amberd-beta-ada', 'deploy', 'ada-deploy.yaml', 'alice', NULL, 2, 'main',
  now() - interval '2 hours', NULL),
 ('beta-new', 'amberd-beta-ada', 'deploy', 'ada-deploy.yaml', 'alice', 21, 2, 'main',
  now() - interval '5 minutes', NULL),
 ('gone-1', 'amberd-gone-ada', 'deploy', 'ada-deploy.yaml', 'carol', 31, 3, 'main',
  now() - interval '9 days', now() - interval '9 days'),
 ('gone-2', 'amberd-gone-ada', 'terminate', 'ada-terminate.yaml', 'carol', 32, NULL, NULL,
  now() - interval '8 days', now() - interval '8 days');

INSERT INTO registered_applications (id, name, application_type, created_by)
VALUES ('11111111-1111-4111-8111-111111111111', 'Billing', 'github_workflow', 'alice');
INSERT INTO registered_application_versions (id, application_id, version, created_by)
VALUES ('22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111', 1, 'alice');
INSERT INTO deployment_instances
    (application_id, application_version_id, instance_name, tier, status, configuration,
     triggered_by, dispatch_reference, current_step)
VALUES ('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
        'billing-tier-1', 1, 'updating', '{}', 'alice',
        '{"operation": "upgrade", "workflow_id": "ada-update.yaml", "run_id": 77}',
        'waiting_for_rollout');
"""


def _query(cursor, sql):
    cursor.execute(sql)
    return cursor.fetchall()


def test_legacy_runs_become_ada_instances_with_an_operation_log():
    name = f"c2ai_test_{uuid.uuid4().hex[:10]}"
    admin = _admin_connection()
    with admin.cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{name}"')
    url = make_url(_SERVER_URL).set(database=name, drivername="postgresql+asyncpg")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        run_migrations(until="0022")
        conn = psycopg2.connect(**psycopg2_connect_kwargs(url.set(drivername="postgresql")))
        with conn, conn.cursor() as cursor:
            cursor.execute(LEGACY)
        run_migrations()
        with conn, conn.cursor() as cursor:
            instances = {
                row[0]: row[1:]
                for row in _query(
                    cursor,
                    f"SELECT instance_name, tier, status, triggered_by,"
                    f" configuration -> 'parameters', configuration -> 'github' ->> 'version'"
                    f" FROM deployment_instances WHERE application_id = '{ADA}'",
                )
            }
            runs = {
                row[0]: row[1:]
                for row in _query(
                    cursor,
                    "SELECT r.id, i.instance_name, r.ended_at IS NULL, r.conclusion, r.operation"
                    " FROM pipeline_runs r LEFT JOIN deployment_instances i"
                    " ON i.id = r.deployment_instance_id",
                )
            }
            [(ada_name, ada_parameters)] = _query(
                cursor,
                "SELECT a.name, count(p.id) FROM registered_applications a"
                " JOIN registered_application_versions v ON v.application_id = a.id"
                " JOIN registered_application_parameters p ON p.application_version_id = v.id"
                f" WHERE a.id = '{ADA}' GROUP BY a.name",
            )
            [(deployments_table,)] = _query(cursor, "SELECT to_regclass('deployments')")
        conn.close()
    finally:
        os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()

    assert (ada_name, ada_parameters) == ("ADA", 4)
    assert instances["amberd-acme-ada"] == (
        1,
        "running",
        "bob",
        {"subdomain": "amberd-acme-ada", "customer_name": "acme", "env_instance": "ada",
         "branch": "v2"},
        "v2",
    )
    assert instances["amberd-beta-ada"][:2] == (2, "deploying")
    assert instances["amberd-gone-ada"][:2] == (3, "terminated")

    assert runs["acme-1"] == ("amberd-acme-ada", False, "success", "deploy")
    assert runs["acme-2"] == ("amberd-acme-ada", False, "success", "update")
    # Only the newest unfinished operation per subdomain stays open.
    assert runs["beta-new"] == ("amberd-beta-ada", True, None, "deploy")
    assert runs["beta-old"][1:3] == (False, "abandoned")
    assert runs["gone-2"][2:] == ("success", "terminate")
    # The registered deployment's in-flight upgrade is in the log too.
    billing = [value for value in runs.values() if value[0] == "billing-tier-1"]
    assert billing == [("billing-tier-1", True, None, "update")]
    assert deployments_table is None
