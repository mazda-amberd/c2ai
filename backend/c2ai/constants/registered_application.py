"""Shared registered-application domain values."""

from enum import Enum


class ApplicationType(str, Enum):
    """Supported reusable application template types."""

    GITHUB_WORKFLOW = "github_workflow"
    CONTAINERIZED = "containerized"


class ApplicationStatus(str, Enum):
    """Lifecycle state of a registered application."""

    ACTIVE = "active"
    DRAFT = "draft"
    DEPRECATED = "deprecated"


class GitHubTriggerMethod(str, Enum):
    """GitHub event used to start a registered workflow."""

    WORKFLOW_DISPATCH = "workflow_dispatch"
    REPOSITORY_DISPATCH = "repository_dispatch"


class ParameterType(str, Enum):
    """Field types that can be rendered in a generated deployment form."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SELECT = "select"
    KEY_VALUE = "key_value"


class ImagePullPolicy(str, Enum):
    """Kubernetes image pull policies supported by container templates."""

    IF_NOT_PRESENT = "IfNotPresent"
    ALWAYS = "Always"
    NEVER = "Never"


class DeploymentInstanceStatus(str, Enum):
    """Lifecycle state of an independently tracked deployment instance."""

    PENDING = "pending"
    DEPLOYING = "deploying"
    RUNNING = "running"
    UPDATING = "updating"
    FAILED = "failed"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    CANCELLED = "cancelled"


class DeploymentStep(str, Enum):
    """Ordered progress stages reported by deployment pipelines."""

    VALIDATING_CONFIGURATION = "validating_configuration"
    CREATING_NAMESPACE = "creating_namespace"
    APPLYING_RESOURCES = "applying_resources"
    WAITING_FOR_ROLLOUT = "waiting_for_rollout"
    VERIFYING_DEPLOYMENT = "verifying_deployment"
    CONFIGURING_DNS = "configuring_dns"
    COMPLETED = "completed"
    FAILED = "failed"


DEPLOYMENT_STEP_ORDER = (
    DeploymentStep.VALIDATING_CONFIGURATION.value,
    DeploymentStep.CREATING_NAMESPACE.value,
    DeploymentStep.APPLYING_RESOURCES.value,
    DeploymentStep.WAITING_FOR_ROLLOUT.value,
    DeploymentStep.VERIFYING_DEPLOYMENT.value,
    DeploymentStep.CONFIGURING_DNS.value,
    DeploymentStep.COMPLETED.value,
)


ACTIVE_DEPLOYMENT_INSTANCE_STATUSES = (
    DeploymentInstanceStatus.PENDING.value,
    DeploymentInstanceStatus.DEPLOYING.value,
    DeploymentInstanceStatus.RUNNING.value,
    DeploymentInstanceStatus.UPDATING.value,
    DeploymentInstanceStatus.TERMINATING.value,
)
