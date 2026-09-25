"""Repositories the registered-application routes depend on.

Routes receive these through ``Depends`` rather than importing them, so a
test replaces one with ``app.dependency_overrides`` and never needs to know
which module the code lives in.
"""

from __future__ import annotations

from types import ModuleType

from c2ai.crud import github_connection as _github_connections
from c2ai.deployments import operations as _operations, repository as _deployments
from c2ai.registration import (
    credentials as _credentials,
    repository as _applications,
    secrets as _secrets,
)


def applications_repository() -> ModuleType:
    return _applications


def credentials_repository() -> ModuleType:
    return _credentials


def secrets_repository() -> ModuleType:
    return _secrets


def deployments_repository() -> ModuleType:
    return _deployments


def operations_repository() -> ModuleType:
    return _operations


def github_connections_repository() -> ModuleType:
    return _github_connections
