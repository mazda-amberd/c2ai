"""API contracts for reusable registered application templates."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from c2ai.constants.registered_application import (
    ApplicationStatus,
    ApplicationType,
    DeploymentInstanceStatus,
    DeploymentStep,
    GitHubTriggerMethod,
    ImagePullPolicy,
    ParameterType,
)

_CONFIG_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_GITHUB_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$"
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_KUBERNETES_SECRET_NAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_ENVIRONMENT_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CONTAINER_IMAGE_TAG_RE = re.compile(
    r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"
)
_DEPLOYMENT_VERSION_RE = re.compile(
    r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,254}$"
)


class _ContractModel(BaseModel):
    """Strict base model used by public registered-application contracts."""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        protected_namespaces=(),
        str_strip_whitespace=True,
    )


class ParameterDefinition(_ContractModel):
    """A field used to generate a deployment form from a template."""

    label: str = Field(..., min_length=1, max_length=200)
    key: str = Field(..., min_length=1, max_length=128, pattern=_CONFIG_KEY_RE.pattern)
    parameter_type: ParameterType = Field(..., alias="type")
    required: bool
    options: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("options")
    @classmethod
    def validate_options(cls, options: list[str]) -> list[str]:
        cleaned = [option.strip() for option in options]
        if any(not option for option in cleaned):
            raise ValueError("parameter options cannot be empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("parameter options must be unique")
        return cleaned

    @model_validator(mode="after")
    def options_are_only_used_for_select(self) -> ParameterDefinition:
        if self.options and self.parameter_type != ParameterType.SELECT:
            raise ValueError("options are only supported for select parameters")
        return self


class ContainerParameterDefinition(ParameterDefinition):
    """Container deployment parameter, optionally carrying a default value."""

    default_value: Any | None = None


class RegisteredParameterDefinition(_ContractModel):
    """User-supplied field definition rendered by the deployment wizard."""

    key: str = Field(..., min_length=1, max_length=128, pattern=_CONFIG_KEY_RE.pattern)
    parameter_type: ParameterType = Field(..., alias="type")


class RegisteredContainerParameterValue(_ContractModel):
    """
    Container environment value fixed at registration.

    A container template carries its own environment, so the value is supplied
    once here instead of being asked for on every deployment.
    """

    key: str = Field(..., min_length=1, max_length=128, pattern=_CONFIG_KEY_RE.pattern)
    value: str = Field(default="", max_length=4096)


class LLMConfigurationCreate(_ContractModel):
    """LLM settings supplied during registration; the token is write-only."""

    endpoint: str = Field(..., min_length=1, max_length=2048)
    api_token: SecretStr = Field(..., min_length=1, max_length=65536)
    # The gateway pins a few models and routes the rest by name prefix, so any
    # name is accepted here; pricing availability is reported separately.
    model_name: str = Field(..., min_length=1, max_length=255)


class LLMConfigurationOut(_ContractModel):
    """Safe LLM settings response; API tokens are intentionally absent."""

    endpoint: str = Field(..., min_length=1, max_length=2048)
    model_name: str = Field(..., min_length=1, max_length=255)


class LLMModelOut(_ContractModel):
    """One model name with the provider it routes to and its pricing state."""

    model_name: str = Field(..., min_length=1, max_length=255)
    provider: str | None = None
    pricing_available: bool
    message: str | None = None


class LLMModelList(_ContractModel):
    """Models Athena can price, offered as suggestions during registration."""

    items: list[LLMModelOut]
    total: int


class ContainerApplicationSecretCreate(_ContractModel):
    """Write-only value and metadata for a managed container secret."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=_KUBERNETES_SECRET_NAME_RE.pattern,
    )
    environment_variable: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_ENVIRONMENT_VARIABLE_RE.pattern,
    )
    secret_value: SecretStr = Field(..., min_length=1, max_length=65536)


class ContainerApplicationSecretUpdate(_ContractModel):
    """Metadata and optional write-only value rotation for a managed secret."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        pattern=_KUBERNETES_SECRET_NAME_RE.pattern,
    )
    environment_variable: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_ENVIRONMENT_VARIABLE_RE.pattern,
    )
    secret_value: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=65536,
    )

    @model_validator(mode="after")
    def require_update(self) -> ContainerApplicationSecretUpdate:
        if (
            self.name is None
            and self.environment_variable is None
            and self.secret_value is None
        ):
            raise ValueError("at least one secret field must be updated")
        return self


class ContainerApplicationSecretOut(_ContractModel):
    """Safe managed-secret response; secret values are intentionally absent."""

    id: UUID
    application_id: UUID
    name: str
    environment_variable: str
    reference: str = Field(..., min_length=1, max_length=512)
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class ContainerApplicationSecretList(_ContractModel):
    """Paginated managed secrets for one containerized application."""

    items: list[ContainerApplicationSecretOut]
    total: int = Field(..., ge=0)
    offset: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=200)


class GitHubWorkflowConfiguration(_ContractModel):
    """Configuration needed to dispatch a GitHub Actions workflow."""

    github_connection_id: str = Field(
        ...,
        alias="github_connection",
        min_length=1,
        max_length=200,
        description="Opaque identifier of an existing GitHub connection.",
    )
    trigger_method: GitHubTriggerMethod
    repository: str = Field(..., min_length=3, max_length=255)
    code_repository: str | None = Field(default=None, min_length=3, max_length=255)
    workflow_file_path: str = Field(..., min_length=1, max_length=512)
    ref: str = Field(default="main", min_length=1, max_length=255)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, repository: str) -> str:
        if not _GITHUB_REPOSITORY_RE.fullmatch(repository):
            raise ValueError(
                f"Workflow repository '{repository}' must use the owner/repository "
                "format"
            )
        return repository

    @field_validator("code_repository")
    @classmethod
    def validate_code_repository(cls, repository: str | None) -> str | None:
        if repository is not None and not _GITHUB_REPOSITORY_RE.fullmatch(repository):
            raise ValueError(
                f"Code repository '{repository}' must use the owner/repository "
                "format"
            )
        return repository

    @field_validator("workflow_file_path")
    @classmethod
    def validate_workflow_file_path(cls, path: str) -> str:
        lowered = path.lower()
        if (
            not path.startswith(".github/workflows/")
            or not lowered.endswith((".yml", ".yaml"))
            or ".." in path.split("/")
        ):
            raise ValueError(
                f"Workflow file '{path}' must be a YAML file under .github/workflows/"
            )
        return path

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, ref: str) -> str:
        if any(character.isspace() for character in ref) or _CONTROL_CHAR_RE.search(ref):
            raise ValueError("ref cannot contain whitespace or control characters")
        return ref


class ContainerEnvironmentVariable(_ContractModel):
    """One non-secret environment variable stored with a container template."""

    key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_ENVIRONMENT_VARIABLE_RE.pattern,
    )
    value: str = Field(..., max_length=10000)


class _ContainerConfigurationBase(_ContractModel):
    """Safe container image, resource, and networking template fields."""

    registry: str = Field(..., min_length=1, max_length=200)
    image_registry: str = Field(..., min_length=1, max_length=512)
    registry_username: str | None = Field(default=None, min_length=1, max_length=255)
    tag: str | None = Field(default=None, min_length=1, max_length=128)
    port: int | None = Field(default=None, ge=1, le=65535)
    pull_policy: ImagePullPolicy
    expose_public_service: bool
    gpu_request: str | None = Field(default=None, min_length=1, max_length=64)
    cpu_request: str | None = Field(default=None, min_length=1, max_length=64)
    memory_request: str | None = Field(default=None, min_length=1, max_length=64)
    scaling: str | None = Field(default=None, min_length=1, max_length=512)
    storage: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("image_registry")
    @classmethod
    def validate_image_registry(cls, repository: str) -> str:
        if (
            "://" in repository
            or any(character.isspace() for character in repository)
            or repository.startswith("/")
            or repository.endswith("/")
        ):
            raise ValueError(
                "image_registry must be an image path without a URL scheme or whitespace"
            )
        return repository

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, tag: str | None) -> str | None:
        if tag is not None and not _CONTAINER_IMAGE_TAG_RE.fullmatch(tag):
            raise ValueError("tag must be a valid container image tag")
        return tag

class ContainerConfigurationCreate(_ContainerConfigurationBase):
    """Container registration fields, including a write-only registry password."""

    registry_username: str = Field(..., min_length=1, max_length=255)
    registry_password: SecretStr = Field(..., min_length=1, max_length=65536)
    tag: str = Field(..., min_length=1, max_length=128)
    port: int = Field(..., ge=1, le=65535)


class ContainerConfigurationOut(_ContainerConfigurationBase):
    """Safe container response; the registry password is intentionally absent."""


class ContainerImageTagOut(_ContractModel):
    """One deployment-ready image tag returned by a container registry."""

    tag: str = Field(..., min_length=1, max_length=128)
    image_reference: str = Field(..., min_length=1, max_length=1024)
    digest: str | None = Field(default=None, min_length=1, max_length=512)
    last_updated: datetime | None = None
    is_default: bool = False


class ContainerImageTagList(_ContractModel):
    """Normalized registry tags for a registered container application."""

    application_id: UUID
    registry: str = Field(..., min_length=1, max_length=200)
    repository: str = Field(..., min_length=1, max_length=512)
    default_tag: str | None = Field(default=None, min_length=1, max_length=255)
    items: list[ContainerImageTagOut]
    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=200)


def _ensure_unique_definition_keys(
    parameters: (
        list[ParameterDefinition]
        | list[RegisteredParameterDefinition]
        | list[RegisteredContainerParameterValue]
    ),
) -> None:
    parameter_keys = [parameter.key for parameter in parameters]
    if len(parameter_keys) != len(set(parameter_keys)):
        raise ValueError("parameter keys must be unique within an application version")


def _ensure_supported_registration_types(
    parameters: list[RegisteredParameterDefinition],
) -> None:
    supported = {
        ParameterType.TEXT,
        ParameterType.NUMBER,
        ParameterType.KEY_VALUE,
    }
    if any(parameter.parameter_type not in supported for parameter in parameters):
        raise ValueError("registration parameters support text, number, or key_value")


class _RegisteredApplicationCreateBase(_ContractModel):
    """Fields shared by both registration payload variants."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        if _CONTROL_CHAR_RE.search(name):
            raise ValueError("name cannot contain control characters")
        return name


class GitHubRegisteredApplicationCreate(_RegisteredApplicationCreateBase):
    """Registration payload for a GitHub Workflow application."""

    application_type: Literal[ApplicationType.GITHUB_WORKFLOW]
    github: GitHubWorkflowConfiguration
    parameters: list[RegisteredParameterDefinition] = Field(
        default_factory=list,
        max_length=200,
    )
    llm: LLMConfigurationCreate

    @model_validator(mode="after")
    def validate_parameter_keys(self) -> GitHubRegisteredApplicationCreate:
        _ensure_unique_definition_keys(self.parameters)
        _ensure_supported_registration_types(self.parameters)
        return self


class ContainerRegisteredApplicationCreate(_RegisteredApplicationCreateBase):
    """Registration payload for a Containerized application."""

    application_type: Literal[ApplicationType.CONTAINERIZED]
    container: ContainerConfigurationCreate
    parameters: list[RegisteredContainerParameterValue] = Field(
        default_factory=list,
        max_length=200,
    )
    llm: LLMConfigurationCreate

    @model_validator(mode="after")
    def validate_parameter_keys(self) -> ContainerRegisteredApplicationCreate:
        _ensure_unique_definition_keys(self.parameters)
        return self


RegisteredApplicationCreate = Annotated[
    Union[GitHubRegisteredApplicationCreate, ContainerRegisteredApplicationCreate],
    Field(discriminator="application_type"),
]


class TierDeploymentSummary(_ContractModel):
    """Number of deployed instances in one tier."""

    tier: str = Field(..., min_length=1, max_length=100)
    instances: int = Field(..., ge=1)


class RegisteredApplicationCatalogItem(_ContractModel):
    """Registered Applications catalog row contract."""

    id: UUID
    name: str
    description: str | None = None
    application_type: ApplicationType
    status: ApplicationStatus
    current_version: int = Field(..., ge=1)
    total_deployed_instances: int = Field(..., ge=0)
    tiers_deployed_to: list[TierDeploymentSummary] = Field(default_factory=list)
    can_delete: bool
    created_at: datetime
    updated_at: datetime


class RegisteredApplicationCatalogResponse(_ContractModel):
    """Paginated Registered Applications catalog response."""

    items: list[RegisteredApplicationCatalogItem]
    total: int = Field(..., ge=0)
    offset: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=200)


class _RegisteredApplicationDetailBase(_ContractModel):
    """Fields returned for a fully hydrated application template."""

    id: UUID
    name: str
    description: str | None = None
    status: ApplicationStatus
    version: int = Field(..., ge=1)
    created_by: str
    created_at: datetime


class GitHubRegisteredApplicationDetail(_RegisteredApplicationDetailBase):
    """Detailed GitHub Workflow template response."""

    application_type: Literal[ApplicationType.GITHUB_WORKFLOW]
    github: GitHubWorkflowConfiguration
    parameters: list[RegisteredParameterDefinition] = Field(default_factory=list)
    llm: LLMConfigurationOut | None = None


class ContainerRegisteredApplicationDetail(_RegisteredApplicationDetailBase):
    """Detailed Containerized template response."""

    application_type: Literal[ApplicationType.CONTAINERIZED]
    container: ContainerConfigurationOut
    parameters: list[RegisteredContainerParameterValue] = Field(default_factory=list)
    llm: LLMConfigurationOut | None = None


RegisteredApplicationDetail = Annotated[
    Union[GitHubRegisteredApplicationDetail, ContainerRegisteredApplicationDetail],
    Field(discriminator="application_type"),
]


class GitHubRepositoryTagList(_ContractModel):
    application_id: UUID
    repository: str
    branches: list[str]
    tags: list[str]
    items: list[str]


class RegisteredApplicationDeploymentCreate(_ContractModel):
    """Deployment-specific values for one registered GitHub workflow."""

    # A GitHub workflow derives its own instance name from the deployment
    # parameters, so this is optional and only overrides the fallback used when
    # the parameters do not derive one.
    instance_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    tier: int = Field(..., ge=1, le=4)
    version: str | None = Field(default=None, min_length=1, max_length=255)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def validate_parameter_count(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        if len(parameters) > 200:
            raise ValueError("at most 200 deployment parameters are supported")
        return parameters

class ContainerRegisteredApplicationDeploymentCreate(_ContractModel):
    """Deployment-specific values for one registered container template."""

    instance_name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        description=(
            "Unique deployment name in the target Tier and the DNS label used for "
            "the generated Athena hostname."
        ),
    )
    version: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_CONTAINER_IMAGE_TAG_RE.pattern,
        description="Container image version/tag selected from the registered registry.",
    )


class RegisteredApplicationDeploymentUpgrade(_ContractModel):
    """Single-field request used to upgrade either supported deployment type."""

    version: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=_DEPLOYMENT_VERSION_RE.pattern,
    )

    @field_validator("version")
    @classmethod
    def validate_version(cls, version: str) -> str:
        value = version.strip()
        if not value or _CONTROL_CHAR_RE.search(value):
            raise ValueError("version must not be blank or contain control characters")
        return value


class RegisteredApplicationDeploymentTerminate(_ContractModel):
    """Explicit destructive confirmation for terminating one deployment."""

    confirmation: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        description="Must exactly match the deployment instance name.",
    )


class RegisteredApplicationDeploymentOut(_ContractModel):
    """Deployment instance returned immediately after pipeline dispatch."""

    id: UUID
    application_id: UUID
    application_name: str
    application_type: ApplicationType
    application_version: int = Field(..., ge=1)
    instance_name: str
    tier: int = Field(..., ge=1, le=4)
    status: str
    configuration: dict[str, Any]
    triggered_by: str
    dispatch_reference: dict[str, Any] | None = None
    current_step: DeploymentStep
    failure_reason: str | None = None
    completed_at: datetime | None = None
    terminated_at: datetime | None = None
    rollback_count: int = Field(default=0, ge=0)
    can_rollback: bool = False
    subdomain: str | None = None
    hostname: str | None = None
    dns_status: Literal[
        "pending",
        "configuring",
        "active",
        "deleting",
        "deleted",
        "failed",
    ] | None = None
    created_at: datetime
    updated_at: datetime


class RegisteredApplicationDeploymentEventOut(_ContractModel):
    """One immutable deployment lifecycle transition."""

    id: UUID
    step: DeploymentStep
    status: DeploymentInstanceStatus
    message: str | None = None
    failure_reason: str | None = None
    created_by: str
    created_at: datetime


class GitHubWorkflowStepOut(_ContractModel):
    """One step reported by GitHub inside a workflow job."""

    number: int = Field(..., ge=1)
    name: str
    status: str
    conclusion: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class GitHubWorkflowJobOut(_ContractModel):
    """One GitHub Actions job/category and its ordered steps."""

    id: int = Field(..., ge=1)
    name: str
    status: str
    conclusion: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    html_url: str | None = None
    steps: list[GitHubWorkflowStepOut] = Field(default_factory=list)


class GitHubWorkflowProgressOut(_ContractModel):
    """Live GitHub Actions run progress shown in deployment history."""

    run_id: int = Field(..., ge=1)
    run_number: int | None = Field(default=None, ge=1)
    name: str | None = None
    display_title: str | None = None
    status: str
    conclusion: str | None = None
    html_url: str | None = None
    event: str | None = None
    head_branch: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    jobs: list[GitHubWorkflowJobOut] = Field(default_factory=list)


class RegisteredApplicationDeploymentDetail(RegisteredApplicationDeploymentOut):
    """Deployment instance with its complete lifecycle history."""

    events: list[RegisteredApplicationDeploymentEventOut] = Field(default_factory=list)
    workflow_progress: GitHubWorkflowProgressOut | None = None
    workflow_progress_error: str | None = None


class RegisteredApplicationDeploymentList(_ContractModel):
    """Paginated deployment instance history."""

    items: list[RegisteredApplicationDeploymentOut]
    total: int = Field(..., ge=0)
    offset: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=200)


class RegisteredApplicationDeploymentProgressUpdate(_ContractModel):
    """Idempotent progress state reported by a deployment pipeline."""

    current_step: DeploymentStep
    status: Literal[
        DeploymentInstanceStatus.DEPLOYING,
        DeploymentInstanceStatus.UPDATING,
        DeploymentInstanceStatus.TERMINATING,
        DeploymentInstanceStatus.TERMINATED,
        DeploymentInstanceStatus.RUNNING,
        DeploymentInstanceStatus.FAILED,
    ]
    message: str | None = Field(default=None, max_length=5000)
    failure_reason: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> RegisteredApplicationDeploymentProgressUpdate:
        if self.current_step == DeploymentStep.FAILED:
            if self.status != DeploymentInstanceStatus.FAILED:
                raise ValueError("the failed step requires failed status")
            if not self.failure_reason:
                raise ValueError("failure_reason is required for a failed deployment")
        elif self.status == DeploymentInstanceStatus.FAILED:
            raise ValueError("failed status requires the failed step")

        if (
            self.current_step == DeploymentStep.COMPLETED
            and self.status
            not in {
                DeploymentInstanceStatus.RUNNING,
                DeploymentInstanceStatus.TERMINATED,
            }
        ):
            raise ValueError("the completed step requires running or terminated status")
        if (
            self.status == DeploymentInstanceStatus.TERMINATED
            and self.current_step != DeploymentStep.COMPLETED
        ):
            raise ValueError("terminated status requires the completed step")
        if (
            self.status == DeploymentInstanceStatus.RUNNING
            and self.current_step != DeploymentStep.COMPLETED
        ):
            raise ValueError("running status requires the completed step")
        if self.current_step != DeploymentStep.FAILED and self.failure_reason:
            raise ValueError("failure_reason is only supported for failed deployments")
        return self
