"""Structural rules the code base relies on (cheap to check, easy to break)."""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "c2ai"

# Repositories only flush: requests, services and jobs own the transaction.
REPOSITORIES = [
    "deployments/repository.py",
    "deployments/operations.py",
    "registration/repository.py",
    "registration/secrets.py",
    "registration/credentials.py",
    "crud/user.py",
    "crud/github_connection.py",
    "crud/application_instance.py",
    "crud/session.py",
]


def _calls(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


@pytest.mark.parametrize("module", REPOSITORIES)
def test_repositories_never_commit_or_roll_back(module):
    assert not {"commit", "rollback"} & _calls(ROOT / module)


@pytest.mark.parametrize(
    "module", ["deployments/status.py", "api/deployments.py"]
)
def test_status_reads_never_call_github(module):
    imported = _imports(ROOT / module)
    forbidden = {"c2ai.deployments.tracking", "c2ai.clients.github_actions"}
    assert not {name for name in imported if any(name.startswith(f) for f in forbidden)}


def test_every_setting_is_read_through_config():
    offenders = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*.py")
        if path.name != "config.py" and "getenv" in _calls(path)
    ]
    assert offenders == []


@pytest.mark.parametrize("module", ["catalog.py", "secrets.py", "deployments.py"])
def test_registered_application_routes_receive_repositories_by_injection(module):
    """Routes get repositories through Depends (repositories.py), never by import."""

    tree = ast.parse((ROOT / "api/registered_applications" / module).read_text())
    repository_modules = {
        ("c2ai.registration", "repository"),
        ("c2ai.registration", "secrets"),
        ("c2ai.registration", "credentials"),
        ("c2ai.deployments", "repository"),
        ("c2ai.deployments", "operations"),
        ("c2ai.crud", "github_connection"),
    }
    imported_modules = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not repository_modules & imported_modules
