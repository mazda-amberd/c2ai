# pylint: disable=import-error
"""SQLAlchemy models for reusable registered application templates."""

from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from c2ai.constants.registered_application import ApplicationStatus

from .base import Base


class RegisteredApplication(Base):
    """Global application identity whose configuration is stored in versions."""

    __tablename__ = "registered_applications"
    __table_args__ = (
        CheckConstraint(
            "application_type IN ('github_workflow', 'containerized')",
            name="ck_registered_applications_type",
        ),
        CheckConstraint(
            "status IN ('active', 'draft', 'deprecated')",
            name="ck_registered_applications_status",
        ),
        CheckConstraint(
            "current_version > 0",
            name="ck_registered_applications_current_version",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    name = Column(String(200), nullable=False)
    application_type = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default=ApplicationStatus.ACTIVE.value)
    current_version = Column(Integer, nullable=False, default=1)
    created_by = Column(String, nullable=False)
    updated_by = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    versions = relationship(
        "RegisteredApplicationVersion",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="RegisteredApplicationVersion.version",
    )
    deployment_instances = relationship(
        "DeploymentInstance",
        back_populates="application",
    )
    managed_secrets = relationship(
        "ContainerApplicationSecret",
        back_populates="application",
        cascade="all, delete-orphan",
    )


Index(
    "uq_registered_applications_name_ci",
    func.lower(RegisteredApplication.name),
    unique=True,
    postgresql_where=RegisteredApplication.deleted_at.is_(None),
)


class GitHubConnection(Base):
    """Reusable GitHub endpoint with a write-only encrypted access token."""

    __tablename__ = "github_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    display_name = Column(String(200), nullable=False)
    connection_url = Column(String(2048), nullable=False)
    access_token_encrypted = Column(LargeBinary, nullable=False)
    created_by = Column(String(255), nullable=False)
    updated_by = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)


Index(
    "uq_github_connections_url_active",
    func.lower(GitHubConnection.connection_url),
    unique=True,
    postgresql_where=text("deleted_at IS NULL"),
)


class RegisteredApplicationVersion(Base):
    """Immutable deployable snapshot of a registered application."""

    __tablename__ = "registered_application_versions"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "version",
            name="uq_registered_application_versions_application_version",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_registered_application_versions_version",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    application = relationship("RegisteredApplication", back_populates="versions")
    github_configuration = relationship(
        "GitHubApplicationConfiguration",
        back_populates="application_version",
        cascade="all, delete-orphan",
        uselist=False,
    )
    container_configuration = relationship(
        "ContainerApplicationConfiguration",
        back_populates="application_version",
        cascade="all, delete-orphan",
        uselist=False,
    )
    parameters = relationship(
        "ApplicationParameterDefinition",
        back_populates="application_version",
        cascade="all, delete-orphan",
        order_by="ApplicationParameterDefinition.position",
    )
    secret_references = relationship(
        "ApplicationSecretReference",
        back_populates="application_version",
        cascade="all, delete-orphan",
        order_by="ApplicationSecretReference.position",
    )
    llm_configuration = relationship(
        "ApplicationLLMConfiguration",
        back_populates="application_version",
        cascade="all, delete-orphan",
        uselist=False,
    )


class GitHubApplicationConfiguration(Base):
    """GitHub Actions configuration for one template version."""

    __tablename__ = "registered_application_github_configs"
    __table_args__ = (
        CheckConstraint(
            "trigger_method IN ('workflow_dispatch', 'repository_dispatch')",
            name="ck_registered_application_github_configs_trigger_method",
        ),
    )

    application_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_application_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    github_connection_id = Column(String(200), nullable=False)
    trigger_method = Column(String(32), nullable=False)
    repository = Column(String(255), nullable=False)
    code_repository = Column(String(255), nullable=True)
    workflow_file_path = Column(String(512), nullable=False)
    ref = Column(String(255), nullable=False, default="main")

    application_version = relationship(
        "RegisteredApplicationVersion", back_populates="github_configuration"
    )


class ContainerApplicationConfiguration(Base):
    """Container image and service defaults for one template version."""

    __tablename__ = "registered_application_container_configs"
    __table_args__ = (
        CheckConstraint(
            "image_pull_policy IN ('IfNotPresent', 'Always', 'Never')",
            name="ck_registered_application_container_configs_pull_policy",
        ),
        CheckConstraint(
            "container_port IS NULL OR container_port BETWEEN 1 AND 65535",
            name="ck_registered_application_container_configs_port",
        ),
    )

    application_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_application_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    registry = Column(String(200), nullable=False)
    registry_credential_id = Column(String(200), nullable=True)
    registry_username = Column(String(255), nullable=True)
    registry_password_encrypted = Column(LargeBinary, nullable=True)
    image_repository = Column(String(512), nullable=False)
    default_image_tag = Column(String(255), nullable=True)
    image_pull_policy = Column(String(32), nullable=False)
    container_port = Column(Integer, nullable=True)
    expose_public_service = Column(Boolean, nullable=False)
    gpu_request = Column(String(64), nullable=True)
    cpu_request = Column(String(64), nullable=True)
    memory_request = Column(String(64), nullable=True)
    scaling = Column(String(512), nullable=True)
    storage = Column(String(512), nullable=True)
    environment_variables = Column(JSONB, nullable=False, default=list)

    application_version = relationship(
        "RegisteredApplicationVersion", back_populates="container_configuration"
    )


class ApplicationLLMConfiguration(Base):
    """LLM endpoint/model metadata and encrypted API token for one version."""

    __tablename__ = "registered_application_llm_configs"

    application_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_application_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    endpoint = Column(String(2048), nullable=False)
    api_token_encrypted = Column(LargeBinary, nullable=False)
    model_name = Column(String(255), nullable=False)

    application_version = relationship(
        "RegisteredApplicationVersion", back_populates="llm_configuration"
    )


class ApplicationParameterDefinition(Base):
    """A generated deployment-form field owned by one template version."""

    __tablename__ = "registered_application_parameters"
    __table_args__ = (
        UniqueConstraint(
            "application_version_id",
            "key",
            name="uq_registered_application_parameters_version_key",
        ),
        UniqueConstraint(
            "application_version_id",
            "position",
            name="uq_registered_application_parameters_version_position",
        ),
        CheckConstraint(
            "position >= 0",
            name="ck_registered_application_parameters_position",
        ),
        CheckConstraint(
            "parameter_type IN ('text', 'number', 'boolean', 'select', 'key_value')",
            name="ck_registered_application_parameters_type",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    application_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_application_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False)
    label = Column(String(200), nullable=False)
    key = Column(String(128), nullable=False)
    parameter_type = Column(String(32), nullable=False)
    required = Column(Boolean, nullable=False, default=False)
    default_value = Column(JSONB, nullable=True)
    options = Column(JSONB, nullable=False, default=list)
    description = Column(Text, nullable=True)
    # Tier number ("1".."4") -> the value used on that tier instead of the default.
    tier_defaults = Column(JSONB, nullable=False, default=dict)

    application_version = relationship(
        "RegisteredApplicationVersion", back_populates="parameters"
    )


class ApplicationSecretReference(Base):
    """Secret metadata/reference; secret values are intentionally not stored."""

    __tablename__ = "registered_application_secret_references"
    __table_args__ = (
        UniqueConstraint(
            "application_version_id",
            "key",
            name="uq_registered_application_secrets_version_key",
        ),
        UniqueConstraint(
            "application_version_id",
            "position",
            name="uq_registered_application_secrets_version_position",
        ),
        CheckConstraint(
            "position >= 0",
            name="ck_registered_application_secrets_position",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    application_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_application_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False)
    label = Column(String(200), nullable=False)
    key = Column(String(128), nullable=False)
    required = Column(Boolean, nullable=False, default=False)
    secret_reference = Column(String(512), nullable=True)

    application_version = relationship(
        "RegisteredApplicationVersion", back_populates="secret_references"
    )


class ContainerApplicationSecret(Base):
    """Metadata and opaque broker reference for a managed container secret."""

    __tablename__ = "container_application_secrets"
    __table_args__ = (
        CheckConstraint(
            "name ~ '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$'",
            name="ck_container_application_secrets_name",
        ),
        CheckConstraint(
            "environment_variable ~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'",
            name="ck_container_application_secrets_environment_variable",
        ),
        Index(
            "uq_container_application_secrets_name_active",
            "application_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_container_application_secrets_env_active",
            "application_id",
            "environment_variable",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_container_application_secrets_application_created",
            "application_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(63), nullable=False)
    environment_variable = Column(String(128), nullable=False)
    secret_reference = Column(String(512), nullable=True)
    created_by = Column(String(255), nullable=False)
    updated_by = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    application = relationship("RegisteredApplication", back_populates="managed_secrets")


class DeploymentInstance(Base):
    """Independent deployment linked to a registered template version."""

    __tablename__ = "deployment_instances"
    # ``updated_at`` carries a server-side ``onupdate``, so after a flushed
    # UPDATE the ORM cannot know its new value and marks the attribute expired
    # even though the session uses ``expire_on_commit=False``. Reading it then
    # emits an implicit lazy SELECT, which an AsyncSession cannot perform
    # outside ``greenlet_spawn``, failing with ``MissingGreenlet``. Fetching
    # server-generated values eagerly (UPDATE ... RETURNING on PostgreSQL)
    # keeps every column loaded after a commit.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint(
            "tier",
            "instance_name",
            name="uq_deployment_instances_tier_instance_name",
        ),
        CheckConstraint(
            "tier BETWEEN 1 AND 4",
            name="ck_deployment_instances_tier",
        ),
        CheckConstraint(
            "status IN ('pending', 'deploying', 'running', 'updating', "
            "'failed', 'terminating', 'terminated', 'cancelled')",
            name="ck_deployment_instances_status",
        ),
        CheckConstraint(
            "current_step IN ('validating_configuration', 'creating_namespace', "
            "'applying_resources', 'waiting_for_rollout', "
            "'verifying_deployment', 'configuring_dns', 'completed', 'failed')",
            name="ck_deployment_instances_current_step",
        ),
        CheckConstraint(
            "(subdomain IS NULL AND hostname IS NULL AND dns_status IS NULL) OR "
            "(subdomain IS NOT NULL AND hostname IS NOT NULL AND dns_status IS NOT NULL)",
            name="ck_deployment_instances_dns_fields",
        ),
        CheckConstraint(
            "dns_status IS NULL OR dns_status IN "
            "('pending', 'configuring', 'active', 'deleting', 'deleted', 'failed')",
            name="ck_deployment_instances_dns_status",
        ),
        CheckConstraint(
            "rollback_count >= 0",
            name="ck_deployment_instances_rollback_count",
        ),
        Index(
            "ix_deployment_instances_application_status",
            "application_id",
            "status",
        ),
        Index(
            "ix_deployment_instances_tier_status",
            "tier",
            "status",
        ),
        Index(
            "uq_deployment_instances_dns_subdomain_active",
            "subdomain",
            unique=True,
            postgresql_where=text(
                "subdomain IS NOT NULL AND status NOT IN ('terminated', 'cancelled')"
            ),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    application_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("registered_application_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    instance_name = Column(String(200), nullable=False)
    tier = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    configuration = Column(JSONB, nullable=False, default=dict)
    # Configuration in effect before the latest upgrade; rollback restores it.
    previous_configuration = Column(JSONB, nullable=True)
    triggered_by = Column(String(255), nullable=False)
    dispatch_reference = Column(JSONB, nullable=True)
    current_step = Column(
        String(64),
        nullable=False,
        default="validating_configuration",
    )
    failure_reason = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    rollback_count = Column(Integer, nullable=False, default=0)
    subdomain = Column(String(63), nullable=True)
    hostname = Column(String(253), nullable=True)
    dns_status = Column(String(32), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    terminated_at = Column(DateTime(timezone=True), nullable=True)

    application = relationship(
        "RegisteredApplication", back_populates="deployment_instances"
    )
    application_version = relationship("RegisteredApplicationVersion")
    events = relationship(
        "DeploymentInstanceEvent",
        back_populates="deployment_instance",
        cascade="all, delete-orphan",
        order_by="DeploymentInstanceEvent.created_at",
    )


class DeploymentInstanceEvent(Base):
    """Immutable status transition for one deployment instance."""

    __tablename__ = "deployment_instance_events"
    __table_args__ = (
        CheckConstraint(
            "step IN ('validating_configuration', 'creating_namespace', "
            "'applying_resources', 'waiting_for_rollout', "
            "'verifying_deployment', 'configuring_dns', 'completed', 'failed')",
            name="ck_deployment_instance_events_step",
        ),
        CheckConstraint(
            "status IN ('pending', 'deploying', 'running', 'updating', "
            "'failed', 'terminating', 'terminated', 'cancelled')",
            name="ck_deployment_instance_events_status",
        ),
        Index(
            "ix_deployment_instance_events_instance_created",
            "deployment_instance_id",
            "created_at",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    deployment_instance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("deployment_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    step = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    message = Column(Text, nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    deployment_instance = relationship("DeploymentInstance", back_populates="events")
