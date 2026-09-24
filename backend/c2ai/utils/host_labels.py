"""
Host label utilities shared by the GitHub client and request schema validation.

Keeping the subdomain preparation logic here (rather than inside the HTTP
client) ensures that schema validators and dispatch code always use the same
slug algorithm as the devops ``prepare`` step.
"""

from __future__ import annotations

import re

_CUSTOMER_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WORKFLOW_ENV_SUFFIX_RE = re.compile(r"^(.*)-([a-z0-9][a-z0-9-]*)$", re.IGNORECASE)


def _slug_segment(raw: str) -> str:
    return _CUSTOMER_SLUG_RE.sub("-", raw.strip().lower()).strip("-")


def workflow_prepare_subdomain(customer_name: str, env_instance: str) -> str:
    """
    Compute the workflow host label matching devops ``prepare``:

    ``amberd-{customer_slug}-{env_slug}`` (slashes and runs of non-alphanumeric
    chars become ``-``, leading/trailing hyphens stripped per segment).
    """
    cn = _slug_segment(customer_name)
    ei = _slug_segment(env_instance)
    return f"amberd-{cn}-{ei}"


def split_workflow_host_label(full: str) -> tuple[str, str | None]:
    """
    Best-effort split of ``amberd-{customer}-{env}`` into its slug components.

    Returns the raw label as ``client_name`` when no workflow prefix is present.
    Returns ``None`` for ``instance_name`` when no trailing ``-{env}`` suffix can
    be identified.
    """
    trimmed = full.strip()
    prefix = "amberd-"
    if not trimmed.lower().startswith(prefix):
        return trimmed, None

    body = trimmed[len(prefix):]
    match = _WORKFLOW_ENV_SUFFIX_RE.match(body)
    if not match:
        return body, None

    return match.group(1), match.group(2).lower()
