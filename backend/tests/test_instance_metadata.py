"""Tests for backend enrichment of client/instance metadata on metrics rows."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from c2ai.schemas.grafana import Instance, Status
from c2ai.services.instance_metadata import enrich_tiers_with_instance_metadata


class _ScalarResult:
    """Minimal stand-in for SQLAlchemy scalar result objects."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_enrich_tiers_with_instance_metadata_prefers_latest_deployment_row():
    """DB deployment history should override host-label parsing."""
    older = SimpleNamespace(
        id=1,
        subdomain=None,
        instance_name="amberd-acme-ada",
        configuration={"parameters": {"customer_name": "acme-old", "env_instance": "ada-old"}},
        created_at=datetime(2026, 1, 1),
    )
    newer = SimpleNamespace(
        id=2,
        subdomain=None,
        instance_name="amberd-acme-ada",
        configuration={"parameters": {"customer_name": "acme", "env_instance": "ada"}},
        created_at=older.created_at + timedelta(days=1),
    )
    db = AsyncMock()
    db.execute.return_value = _ScalarResult([newer, older])

    tiers = {
        "Tier 1": [
            Instance(
                id=1,
                name="ada",
                nodename="amberd-acme-ada",
                cpu=0.0,
                memory=0.0,
                gpu=0.0,
                status=Status.HEALTHY,
            )
        ]
    }

    enriched = await enrich_tiers_with_instance_metadata(db, tiers)
    instance = enriched["Tier 1"][0]

    assert instance.client_name == "acme"
    assert instance.instance_name == "ada"


@pytest.mark.asyncio
async def test_enrich_tiers_with_instance_metadata_falls_back_to_host_label_parsing():
    """Instances without deployment rows still expose backend-provided metadata."""
    db = AsyncMock()
    db.execute.return_value = _ScalarResult([])

    tiers = {
        "Tier 1": [
            Instance(
                id=1,
                name="ada",
                nodename="amberd-client-prod",
                cpu=0.0,
                memory=0.0,
                gpu=0.0,
                status=Status.HEALTHY,
            ),
            Instance(
                id=2,
                name="ada",
                nodename="standalone",
                cpu=0.0,
                memory=0.0,
                gpu=0.0,
                status=Status.WARNING,
            ),
        ]
    }

    enriched = await enrich_tiers_with_instance_metadata(db, tiers)
    parsed = enriched["Tier 1"][0]
    raw = enriched["Tier 1"][1]

    assert parsed.client_name == "client"
    assert parsed.instance_name == "prod"
    assert raw.client_name == "standalone"
    assert raw.instance_name is None
