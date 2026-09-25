"""Pydantic schemas for deployment/pipeline API endpoints."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

# Shared host-label rule: 3-63 chars, lowercase alphanumeric + hyphens,
# no leading/trailing hyphen.  Mirrors frontend WORKFLOW_HOST_LABEL_RE.
SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$")

# App / workload name for log queries (deployment or Ray cluster display name).
DEPLOYMENT_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,253}$")

# Git branch: printable non-whitespace, max 255 chars.
_BRANCH_RE = re.compile(r"^[^\x00-\x1f\x7f\s]+$")

# Domain / FQDN: two or more dot-separated labels, each label alphanumeric + hyphens.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)

# env_instance: one or more lowercase alphanumeric / hyphen chars (e.g. "ada", "ada-1").
_ENV_INSTANCE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_ENV_INSTANCE_MAX_LEN = 32


def _validate_subdomain_value(v: str) -> str:
    if not SUBDOMAIN_RE.match(v):
        raise ValueError(
            "subdomain must be 3-63 lowercase alphanumeric characters or hyphens, "
            "and must start and end with an alphanumeric character"
        )
    return v


class DeployRequest(BaseModel):
    """Request body for triggering a new deployment or update."""

    branch: str = Field(..., description="Git branch to deploy")
    subdomain: str = Field(
        ...,
        description="Workflow host label (e.g. amberd-customer-env)",
    )
    customer_name: str = Field(..., description="Customer identifier")
    domain: str = Field(..., description="Target domain, e.g. amberd.ai")
    env_instance: str = Field(
        ...,
        min_length=1,
        description="Environment / instance slug (required; e.g. prod, qa, ada).",
    )
    tier: int = Field(..., ge=1, le=4, description="Numeric tier index (1-4)")

    @field_validator("branch", "customer_name", "domain", "env_instance", "subdomain", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, v: str) -> str:
        if not v:
            raise ValueError("branch is required")
        if len(v) > 255:
            raise ValueError("branch must be at most 255 characters")
        if not _BRANCH_RE.match(v):
            raise ValueError("branch must contain only printable non-whitespace characters")
        return v

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, v: str) -> str:
        if not v:
            raise ValueError("customer_name is required")
        if len(v) > 200:
            raise ValueError("customer_name must be at most 200 characters")
        return v

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        if not v:
            raise ValueError("domain is required")
        if len(v) > 253:
            raise ValueError("domain must be at most 253 characters")
        if not _DOMAIN_RE.match(v.lower()):
            raise ValueError(
                "domain must be a valid hostname (e.g. amberd.ai)"
            )
        return v

    @field_validator("env_instance")
    @classmethod
    def validate_env_instance(cls, v: str) -> str:
        lower = v.lower()
        if not lower:
            raise ValueError("env_instance is required")
        if len(lower) > _ENV_INSTANCE_MAX_LEN:
            raise ValueError(
                f"env_instance must be at most {_ENV_INSTANCE_MAX_LEN} characters"
            )
        if not _ENV_INSTANCE_RE.match(lower):
            raise ValueError(
                "env_instance must start with alphanumeric and contain only "
                "lowercase letters, digits, and hyphens"
            )
        return v

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        return _validate_subdomain_value(v)

    @model_validator(mode="after")
    def derived_host_label_is_valid(self) -> DeployRequest:
        from c2ai.utils.host_labels import workflow_prepare_subdomain

        expected = workflow_prepare_subdomain(self.customer_name, self.env_instance)
        if not SUBDOMAIN_RE.match(expected):
            raise ValueError(
                "customer_name and env_instance produce an invalid or overlong host label "
                f"(computed {expected!r}; max 63 DNS label chars)"
            )
        return self


class TerminateRequest(BaseModel):
    """Request body for terminating a live deployment."""

    subdomain: str = Field(
        ...,
        description="Workflow host label of the deployment to terminate",
    )

    @field_validator("subdomain", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        return _validate_subdomain_value(v)


class MoveTierRequest(BaseModel):
    """
    Request body for moving an existing deployment to another tier.

    Attributes:
        subdomain (str): Workflow host label of the deployment to migrate.
        tier (int): Target numeric tier index in the supported move-to-tier range.
    """

    subdomain: str = Field(
        ...,
        description="Workflow host label of the deployment to migrate",
    )
    tier: int = Field(..., ge=1, le=3, description="Target numeric tier index (1-3)")

    @field_validator("subdomain", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        """
        Strip surrounding whitespace from the subdomain field.

        Args:
            v (object): Raw subdomain field value.

        Returns:
            object: Stripped string value, or the original value when non-string.
        """
        return v.strip() if isinstance(v, str) else v

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        """
        Validate the move-tier subdomain field.

        Args:
            v (str): Candidate subdomain value.

        Returns:
            str: Validated subdomain value.

        Raises:
            ValueError: If the subdomain does not match Athena's host-label rules.
        """
        return _validate_subdomain_value(v)


# ---------------------------------------------------------------------------
# Pipeline run response schemas
# ---------------------------------------------------------------------------

class PipelineCancelRequest(BaseModel):
    """Cancel an in-flight GitHub Actions run linked to a ``pipeline_runs`` row."""

    pipeline_run_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Primary key of the pipeline_runs row (UUID correlation id)",
    )


class PipelineRunOut(BaseModel):
    """Response schema returned immediately after triggering an operation."""

    id: str
    subdomain: str
    operation: str
    event_type: str
    triggered_by: str
    # The registered application being deployed; None for ADA, whose cards
    # are titled by customer instead.
    application_name: str | None = None
    run_id: int | None = None
    tier: int | None = None
    branch: str | None = None
    dispatched_at: str | None = None
    ended_at: str | None = None


class PipelineStatusOut(PipelineRunOut):
    """
    Full status response from GET /api/pipeline/status or /api/pipeline/active.

    Fields from GH API are None when the run_id has not yet been resolved or
    when the GH API call fails.
    """

    gh_status: str | None = None
    gh_conclusion: str | None = None
    run_url: str | None = None
    active_job: str | None = None
    current_step: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


# ---------------------------------------------------------------------------
# Legacy schemas kept for backward compatibility (webhook, old list endpoint)
# ---------------------------------------------------------------------------

class DeploymentOut(BaseModel):
    """Legacy response schema for the old deployments table."""

    id: int
    subdomain: str
    customer_name: str
    env_instance: str
    tier: int
    branch: str
    domain: str
    status: str
    created_at: str | None = None
    completed_at: str | None = None


class WebhookPayload(BaseModel):
    """Inbound webhook payload sent by GitHub Actions on deployment completion."""

    status: str = Field(
        ...,
        description="Terminal outcome: 'success', 'failed', or 'cancelled'",
    )
