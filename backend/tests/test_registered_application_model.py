"""Structural tests for the registered-application persistence model."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ApplicationParameterDefinition,
    ApplicationSecretReference,
    ContainerApplicationConfiguration,
    ContainerApplicationSecret,
    DeploymentInstance,
    DeploymentInstanceEvent,
    GitHubApplicationConfiguration,
    RegisteredApplication,
    RegisteredApplicationVersion,
)


def test_registered_application_tables_compile_for_postgresql():
    tables = [
        RegisteredApplication.__table__,
        RegisteredApplicationVersion.__table__,
        GitHubApplicationConfiguration.__table__,
        ContainerApplicationConfiguration.__table__,
        ApplicationLLMConfiguration.__table__,
        ApplicationParameterDefinition.__table__,
        ApplicationSecretReference.__table__,
        ContainerApplicationSecret.__table__,
        DeploymentInstance.__table__,
        DeploymentInstanceEvent.__table__,
    ]

    statements = [
        str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in tables
    ]

    assert all("CREATE TABLE" in statement for statement in statements)
    assert "registered_application_llm_configs" in statements[4]
    assert "JSONB" in statements[5]
    assert "container_application_secrets" in statements[7]
    assert "deployment_instances" in statements[8]
    assert "deployment_instance_events" in statements[9]


def test_registered_application_name_has_case_insensitive_unique_index():
    indexes = {index.name: index for index in RegisteredApplication.__table__.indexes}

    name_index = indexes["uq_registered_applications_name_ci"]
    assert name_index.unique is True
    assert "lower" in str(next(iter(name_index.expressions))).lower()


def test_template_children_are_deleted_with_their_version():
    child_tables = [
        GitHubApplicationConfiguration.__table__,
        ContainerApplicationConfiguration.__table__,
        ApplicationLLMConfiguration.__table__,
        ApplicationParameterDefinition.__table__,
        ApplicationSecretReference.__table__,
    ]

    for table in child_tables:
        foreign_key = next(iter(table.foreign_keys))
        assert foreign_key.target_fullname == "registered_application_versions.id"
        assert foreign_key.ondelete == "CASCADE"


def test_secret_reference_table_cannot_store_secret_values():
    columns = set(ApplicationSecretReference.__table__.columns.keys())

    assert "secret_reference" in columns
    assert "value" not in columns
    assert "secret_value" not in columns


def test_managed_container_secrets_store_only_metadata_and_provider_reference():
    columns = set(ContainerApplicationSecret.__table__.columns.keys())

    assert "secret_reference" in columns
    assert "environment_variable" in columns
    assert "value" not in columns
    assert "secret_value" not in columns

    indexes = {
        index.name: index for index in ContainerApplicationSecret.__table__.indexes
    }
    assert indexes["uq_container_application_secrets_name_active"].unique is True
    assert indexes["uq_container_application_secrets_env_active"].unique is True

    foreign_key = next(iter(ContainerApplicationSecret.__table__.foreign_keys))
    assert foreign_key.target_fullname == "registered_applications.id"
    assert foreign_key.ondelete == "CASCADE"


def test_template_version_has_only_one_configuration_of_each_type():
    github_relationship = RegisteredApplicationVersion.github_configuration.property
    container_relationship = RegisteredApplicationVersion.container_configuration.property
    llm_relationship = RegisteredApplicationVersion.llm_configuration.property

    assert github_relationship.uselist is False
    assert container_relationship.uselist is False
    assert llm_relationship.uselist is False


def test_llm_configuration_stores_encrypted_token_only():
    columns = set(ApplicationLLMConfiguration.__table__.columns.keys())

    assert "endpoint" in columns
    assert "model_name" in columns
    assert "api_token_encrypted" in columns
    assert "api_token" not in columns


def test_container_configuration_stores_encrypted_registry_password_only():
    columns = set(ContainerApplicationConfiguration.__table__.columns.keys())

    assert "registry_username" in columns
    assert "registry_password_encrypted" in columns
    assert "registry_password" not in columns
    assert "cpu_request" in columns
    assert "memory_request" in columns
    assert "scaling" in columns
    assert "storage" in columns
    assert "environment_variables" in columns


def test_catalog_persistence_supports_soft_delete_and_deployment_links():
    assert "deleted_at" in RegisteredApplication.__table__.columns

    deployment_columns = DeploymentInstance.__table__.columns
    assert deployment_columns["application_id"].nullable is False
    assert deployment_columns["application_version_id"].nullable is False
    assert deployment_columns["tier"].nullable is False
    assert deployment_columns["status"].nullable is False
    assert deployment_columns["current_step"].nullable is False
    assert deployment_columns["failure_reason"].nullable is True
    assert deployment_columns["completed_at"].nullable is True
    assert deployment_columns["rollback_count"].nullable is False
    assert deployment_columns["subdomain"].nullable is True
    assert deployment_columns["hostname"].nullable is True
    assert deployment_columns["dns_status"].nullable is True
    dns_constraint = next(
        constraint
        for constraint in DeploymentInstance.__table__.constraints
        if constraint.name == "ck_deployment_instances_dns_status"
    )
    assert "deleting" in str(dns_constraint.sqltext)
    assert "deleted" in str(dns_constraint.sqltext)

    deployment_indexes = {
        index.name: index for index in DeploymentInstance.__table__.indexes
    }
    assert deployment_indexes[
        "uq_deployment_instances_dns_subdomain_active"
    ].unique is True

    event_foreign_key = next(iter(DeploymentInstanceEvent.__table__.foreign_keys))
    assert event_foreign_key.target_fullname == "deployment_instances.id"
    assert event_foreign_key.ondelete == "CASCADE"
