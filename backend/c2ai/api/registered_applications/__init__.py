"""``/api/registered-applications``: the catalog, managed secrets, and deployments.

Sub-routers are included in an order that keeps static paths such as
``/deployments`` and ``/llm-models`` ahead of ``/{application_id}``.
"""

from fastapi import APIRouter

from c2ai.api.registered_applications import catalog, deployments, secrets

router = APIRouter()
router.include_router(deployments.router)
router.include_router(secrets.router)
router.include_router(catalog.router)
