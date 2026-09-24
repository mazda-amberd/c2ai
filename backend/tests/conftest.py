"""
Shared test fixtures for Athena backend tests.

All frames use real k8s/RayCluster label shapes from docs/grafana-k8s-exploration.md:
  - CPU/memory series: labels = {namespace, owner_name}
  - GPU series (ray_node_gpus_utilization): labels = {namespace, ray_io_cluster}
"""

import os

# `c2ai.db.session` creates the async engine at import time; tests never need a real DB
# for routes that mock CRUD / clients.
os.environ.setdefault(
    "LOCAL_DATABASE_URL",
    "postgresql+asyncpg://athena:athena@127.0.0.1:5432/athena_test",
)
os.environ.setdefault(
    "ATHENA_AUTH_SECRET",
    "unit-test-secret-not-for-production",
)
os.environ.setdefault("ATHENA_FINANCIAL_INGESTION_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient

from c2ai.schemas.grafana import (
    GrafanaField,
    GrafanaFieldLabels,
    GrafanaFrame,
    GrafanaFrameData,
    GrafanaFrameSchema,
    GrafanaQueryResult,
    GrafanaResponse,
)
from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser, get_access_token, get_current_user_token
from c2ai.core.exception_handlers import attach_exception_handlers

# Match production `main.py`: handlers convert AppException / validation to JSON.
attach_exception_handlers(app)

_FAKE_USER = AthenaTokenUser(identifier="test-user", service="athena")


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def deploy_auth_client(test_client):
    """Test client with JWT dependencies bypassed (deploy routes require a token)."""

    async def _token_override():
        return "test-token"

    def _user_override():
        return _FAKE_USER

    app.dependency_overrides[get_access_token] = _token_override
    app.dependency_overrides[get_current_user_token] = _user_override
    yield test_client
    app.dependency_overrides.pop(get_access_token, None)
    app.dependency_overrides.pop(get_current_user_token, None)


def _make_raycluster_frame(ref_id: str, owner_name: str, namespace: str, value: float) -> GrafanaFrame:
    """Helper: one frame as returned by CPU/memory queries (namespace + owner_name)."""
    return GrafanaFrame(
        schema=GrafanaFrameSchema(
            refId=ref_id,
            fields=[
                GrafanaField(name="Time", type="time"),
                GrafanaField(
                    name="Value",
                    type="number",
                    labels=GrafanaFieldLabels(owner_name=owner_name, namespace=namespace),
                ),
            ],
        ),
        data=GrafanaFrameData(values=[[1768233053154], [value]]),
    )


def _make_gpu_frame(ref_id: str, ray_io_cluster: str, namespace: str, value: float) -> GrafanaFrame:
    """Helper: one frame as returned by ray_node_gpus_utilization (namespace + ray_io_cluster)."""
    return GrafanaFrame(
        schema=GrafanaFrameSchema(
            refId=ref_id,
            fields=[
                GrafanaField(name="Time", type="time"),
                GrafanaField(
                    name="Value",
                    type="number",
                    labels=GrafanaFieldLabels(ray_io_cluster=ray_io_cluster, namespace=namespace),
                ),
            ],
        ),
        data=GrafanaFrameData(values=[[1768233053154], [value]]),
    )


# ---------------------------------------------------------------------------
# Legacy frame fixtures kept for extract_instances tests that cover the
# node ownership path (values still work because extract_instances falls back
# to owner_name → ray_io_cluster → "instance-N" and namespace → nodename).
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_cpu_frame():
    """CPU frame: RayCluster qwen-496gt in tier1 using 4 cores/sec."""
    return _make_raycluster_frame("A", "qwen-496gt", "tier1", 4.0)


@pytest.fixture
def sample_memory_frame():
    """Memory frame: RayCluster qwen-496gt in tier1 using 40 GB."""
    return _make_raycluster_frame("A", "qwen-496gt", "tier1", 40.0)


@pytest.fixture
def sample_gpu_frame():
    """GPU frame: RayCluster qwen-496gt in tier1 at 0.5 utilisation (50%)."""
    return _make_gpu_frame("A", "qwen-496gt", "tier1", 0.5)


# ---------------------------------------------------------------------------
# Multi-tier response fixtures used by combine_metrics / TestPercentageCalculations.
# Values are RAW k8s units: CPU in cores/sec, memory in GB, GPU in 0-1 fraction.
# The default caps are ATHENA_CPU_CORES_CAP=8, ATHENA_MEMORY_GB_CAP=80.
# ---------------------------------------------------------------------------

def _make_tier_total_gpu_frame(label_tier: str, value: float) -> GrafanaFrame:
    """One frame as returned by tier-total GPU instant query (label_tier + scalar)."""
    return GrafanaFrame(
        schema=GrafanaFrameSchema(
            refId="A",
            fields=[
                GrafanaField(name="Time", type="time"),
                GrafanaField(
                    name="Value",
                    type="number",
                    labels=GrafanaFieldLabels(label_tier=label_tier),
                ),
            ],
        ),
        data=GrafanaFrameData(values=[[1768233053154], [value]]),
    )


@pytest.fixture
def sample_gpu_tier_totals_response():
    """Tier-total GPU: ref A with three series (tier1 / tier2 / tier3)."""
    return GrafanaResponse(
        results={
            "A": GrafanaQueryResult(
                status=200,
                frames=[
                    _make_tier_total_gpu_frame("tier1", 0.5),
                    _make_tier_total_gpu_frame("tier2", 0.5),
                    _make_tier_total_gpu_frame("tier3", 0.5),
                ],
            ),
        }
    )


@pytest.fixture
def sample_grafana_response():
    """
    Generic Grafana response used by tests that need any valid response shape.
    One RayCluster per tier at low GPU utilisation (0-1 range).
    """
    return GrafanaResponse(
        results={
            "A": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("A", "qwen-496gt", "tier1", 0.5)],
            ),
            "B": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("B", "qwen-hrcg7", "tier2", 0.5)],
            ),
            "C": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("C", "qwen-h9spf", "tier3", 0.5)],
            ),
        }
    )


@pytest.fixture
def empty_grafana_response():
    """Empty Grafana response (no frames for any tier)."""
    return GrafanaResponse(
        results={
            "A": GrafanaQueryResult(status=200, frames=[]),
            "B": GrafanaQueryResult(status=200, frames=[]),
            "C": GrafanaQueryResult(status=200, frames=[]),
        }
    )


@pytest.fixture
def sample_gpu_attribution_empty_bundle():
    """Shape of ``fetch_gpu_per_app_data``: panel 18 only (ref A)."""
    return GrafanaResponse(
        results={
            "A": GrafanaQueryResult(status=200, frames=[]),
        }
    )


@pytest.fixture
def sample_cpu_used_response():
    """
    CPU response (cores/sec from container_cpu_usage_seconds_total + RayCluster join).

    Tier 1: qwen-496gt → 4 cores  (4/8 cap = 50%)
    Tier 2: qwen-hrcg7 → 8 cores  (8/8 cap = 100%, clamped)
    Tier 3: qwen-h9spf → 6 cores  (6/8 cap = 75%)
    """
    return GrafanaResponse(
        results={
            "A": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("A", "qwen-496gt", "tier1", 4.0)],
            ),
            "B": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("B", "qwen-hrcg7", "tier2", 8.0)],
            ),
            "C": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("C", "qwen-h9spf", "tier3", 6.0)],
            ),
        }
    )


@pytest.fixture
def sample_memory_used_response():
    """
    Memory response (GB from container_memory_working_set_bytes + RayCluster join).

    Tier 1: qwen-496gt → 40 GB  (40/80 cap = 50%)
    Tier 2: qwen-hrcg7 → 80 GB  (80/80 cap = 100%, clamped)
    Tier 3: qwen-h9spf → 60 GB  (60/80 cap = 75%)
    """
    return GrafanaResponse(
        results={
            "A": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("A", "qwen-496gt", "tier1", 40.0)],
            ),
            "B": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("B", "qwen-hrcg7", "tier2", 80.0)],
            ),
            "C": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("C", "qwen-h9spf", "tier3", 60.0)],
            ),
        }
    )


@pytest.fixture
def sample_cpu_total_response():
    """CPU total (sum of all containers per tier namespace), raw cores/sec."""
    return GrafanaResponse(
        results={
            "A": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("A", "qwen-496gt", "tier1", 8.0)],
            ),
            "B": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("B", "qwen-hrcg7", "tier2", 8.0)],
            ),
            "C": GrafanaQueryResult(
                status=200,
                frames=[_make_raycluster_frame("C", "qwen-h9spf", "tier3", 8.0)],
            ),
        }
    )
