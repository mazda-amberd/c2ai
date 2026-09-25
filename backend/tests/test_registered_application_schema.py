"""Validation tests for registered-application API contracts."""

import pytest
from pydantic import TypeAdapter, ValidationError

from c2ai.constants.registered_application import (
    ApplicationType,
    ImagePullPolicy,
)
from c2ai.schemas.registered_application import (
    ContainerApplicationSecretCreate,
    ContainerApplicationSecretOut,
    ContainerApplicationSecretUpdate,
    ContainerRegisteredApplicationCreate,
    ContainerRegisteredApplicationDeploymentCreate,
    GitHubRegisteredApplicationCreate,
    RegisteredApplicationCreate,
    RegisteredApplicationDeploymentCreate,
    RegisteredApplicationDeploymentTerminate,
    RegisteredApplicationDeploymentUpgrade,
)

_CREATE_ADAPTER = TypeAdapter(RegisteredApplicationCreate)


def _github_payload() -> dict:
    return {
        "application_type": "github_workflow",
        "name": " Example Chatbot ",
        "description": " Customer support workflow ",
        "github": {
            "github_connection": "github-app-1",
            "trigger_method": "workflow_dispatch",
            "repository": "amberd-ai/example-chatbot",
            "workflow_file_path": ".github/workflows/deploy.yml",
            "ref": "main",
        },
        "parameters": [
            {
                "key": "tier",
                "type": "text",
            }
        ],
        "llm": {
            "endpoint": "https://llm.example.com/v1",
            "api_token": "write-only-token",
            "model_name": "qwen3-coder-next",
        },
    }


@pytest.mark.parametrize("code_repository", [None, "amberd-ai/application"])
def test_github_code_repository_is_optional(code_repository):
    payload = _github_payload()
    payload["github"]["code_repository"] = code_repository
    result = GitHubRegisteredApplicationCreate.model_validate(payload)
    assert result.github.code_repository == code_repository


@pytest.mark.parametrize("code_repository", ["amberd-ai", "https://github.com/amberd-ai/app", "a/b/c", ""])
def test_github_code_repository_requires_owner_and_repo(code_repository):
    payload = _github_payload()
    payload["github"]["code_repository"] = code_repository
    with pytest.raises(ValidationError):
        GitHubRegisteredApplicationCreate.model_validate(payload)


def _container_payload() -> dict:
    return {
        "application_type": "containerized",
        "name": "chat-service",
        "container": {
            "registry": "Docker Hub",
            "image_registry": "amberd/chat-service",
            "registry_username": "amberd",
            "registry_password": "write-only-registry-password",
            "tag": "1.2.3",
            "pull_policy": "IfNotPresent",
            "port": 8080,
            "expose_public_service": True,
            "cpu_request": "500m",
            "memory_request": "512Mi",
            "scaling": "3",
            "storage": "10Gi",
        },
        "parameters": [{"key": "LOG_LEVEL", "value": "info"}],
        "llm": {
            "endpoint": "https://llm.example.com/v1",
            "api_token": "write-only-llm-token",
            "model_name": "qwen3-6",
        },
    }


def test_github_registration_contract_is_discriminated_and_strict():
    application = _CREATE_ADAPTER.validate_python(_github_payload())

    assert isinstance(application, GitHubRegisteredApplicationCreate)
    assert application.application_type == ApplicationType.GITHUB_WORKFLOW
    assert application.name == "Example Chatbot"
    assert application.github.github_connection_id == "github-app-1"
    assert application.parameters[0].parameter_type.value == "text"
    assert application.llm.endpoint == "https://llm.example.com/v1"
    assert application.llm.api_token.get_secret_value() == "write-only-token"
    assert "write-only-token" not in repr(application)


def test_container_registration_uses_explicit_template_fields():
    application = _CREATE_ADAPTER.validate_python(_container_payload())

    assert isinstance(application, ContainerRegisteredApplicationCreate)
    assert application.application_type == ApplicationType.CONTAINERIZED
    assert application.container.port == 8080
    assert application.container.pull_policy == ImagePullPolicy.IF_NOT_PRESENT
    assert application.parameters[0].key == "LOG_LEVEL"
    assert application.llm.model_name == "qwen3-6"
    assert "write-only-registry-password" not in repr(application)
    assert "write-only-llm-token" not in repr(application)


def test_container_registration_takes_parameter_values_not_types():
    payload = _container_payload()
    payload["parameters"] = [
        {"key": "LOG_LEVEL", "value": "info"},
        {"key": "FEATURE_FLAG", "value": ""},
    ]

    application = _CREATE_ADAPTER.validate_python(payload)

    assert [(p.key, p.value) for p in application.parameters] == [
        ("LOG_LEVEL", "info"),
        ("FEATURE_FLAG", ""),
    ]


def test_container_registration_rejects_a_typed_parameter_definition():
    payload = _container_payload()
    payload["parameters"] = [{"key": "LOG_LEVEL", "type": "text"}]

    with pytest.raises(ValidationError):
        _CREATE_ADAPTER.validate_python(payload)


def test_container_deployment_contract_is_instance_version_and_customer():
    payload = ContainerRegisteredApplicationDeploymentCreate(
        instance_name=" chat-service-tier-2 ",
        version=" 2.0.0 ",
        customer_name=" Acme Corp ",
    )

    assert payload.model_dump() == {
        "customer_name": "Acme Corp",
        "instance_name": "chat-service-tier-2",
        "version": "2.0.0",
    }
    # The customer is optional for API callers; the form always sends it.
    assert ContainerRegisteredApplicationDeploymentCreate(
        instance_name="chat-service-tier-2", version="2.0.0"
    ).customer_name is None


@pytest.mark.parametrize(
    "extra_field",
    [
        {"tier": 2},
        {"container_port": 9090},
        {"environment_variables": {"LOG_LEVEL": "debug"}},
        {"subdomain": "another-host"},
        {"registry_password": "must-not-be-accepted"},
    ],
)
def test_container_deployment_rejects_template_and_context_overrides(extra_field):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContainerRegisteredApplicationDeploymentCreate(
            instance_name="chat-service-tier-2",
            version="2.0.0",
            **extra_field,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instance_name", "Uppercase"),
        ("instance_name", "host.example.com"),
        ("version", "two words"),
        ("version", "-invalid"),
    ],
)
def test_container_deployment_validates_dns_label_and_image_tag(field, value):
    values = {
        "instance_name": "chat-service-tier-2",
        "version": "2.0.0",
        field: value,
    }
    with pytest.raises(ValidationError):
        ContainerRegisteredApplicationDeploymentCreate(**values)


@pytest.mark.parametrize("removed_field", ["secrets", "managed_secret_ids", "container"])
def test_github_deployment_rejects_removed_legacy_fields(removed_field: str):
    values = {
        "instance_name": "release-prod",
        "tier": 2,
        "parameters": {},
        removed_field: [] if removed_field != "container" else {},
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RegisteredApplicationDeploymentCreate(**values)


@pytest.mark.parametrize(
    "removed_field",
    ["secrets"],
)
def test_container_registration_rejects_removed_definition_fields(removed_field: str):
    payload = _container_payload()
    payload[removed_field] = []

    with pytest.raises(ValidationError) as exc_info:
        _CREATE_ADAPTER.validate_python(payload)

    assert any(
        error["loc"][-1] == removed_field and error["type"] == "extra_forbidden"
        for error in exc_info.value.errors()
    )


@pytest.mark.parametrize("field", ["tag", "port"])
def test_container_registration_requires_registry_and_image_fields(field: str):
    payload = _container_payload()
    del payload["container"][field]

    with pytest.raises(ValidationError):
        _CREATE_ADAPTER.validate_python(payload)


def test_a_public_image_needs_no_registry_login_but_a_password_needs_a_username():
    payload = _container_payload()
    del payload["container"]["registry_username"], payload["container"]["registry_password"]
    public = _CREATE_ADAPTER.validate_python(payload)
    assert (public.container.registry_username, public.container.registry_password) == (None, None)

    payload["container"]["registry_password"] = "secret"
    with pytest.raises(ValidationError, match="a registry password needs a registry username"):
        _CREATE_ADAPTER.validate_python(payload)


def test_container_registration_rejects_duplicate_parameter_keys():
    payload = _container_payload()
    payload["parameters"].append({"key": "LOG_LEVEL", "value": "debug"})

    with pytest.raises(ValidationError, match="parameter keys must be unique"):
        _CREATE_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "model_name",
    ["gemini-2.5-pro", "claude-sonnet-4-6", "gpt-4o", "qwen3-6"],
)
def test_registration_accepts_any_gateway_routed_model_name(model_name: str):
    payload = _github_payload()
    payload["llm"]["model_name"] = model_name

    result = GitHubRegisteredApplicationCreate.model_validate(payload)

    assert result.llm.model_name == model_name


def test_registration_still_requires_a_model_name_within_bounds():
    payload = _github_payload()
    payload["llm"]["model_name"] = "x" * 256

    with pytest.raises(ValidationError):
        GitHubRegisteredApplicationCreate.model_validate(payload)


@pytest.mark.parametrize("field", ["endpoint", "api_token", "model_name"])
def test_container_registration_requires_complete_llm_configuration(field: str):
    payload = _container_payload()
    del payload["llm"][field]

    with pytest.raises(ValidationError):
        _CREATE_ADAPTER.validate_python(payload)


def test_github_registration_rejects_duplicate_parameter_keys():
    payload = _github_payload()
    payload["parameters"].append(dict(payload["parameters"][0]))

    with pytest.raises(ValidationError) as exc_info:
        _CREATE_ADAPTER.validate_python(payload)

    messages = [error["msg"] for error in exc_info.value.errors()]
    assert any("parameter keys must be unique" in message for message in messages)


def test_github_registration_rejects_removed_secret_references():
    payload = _github_payload()
    payload["secrets"] = []

    with pytest.raises(ValidationError) as exc_info:
        _CREATE_ADAPTER.validate_python(payload)

    assert any(
        error["loc"][-1] == "secrets" and error["type"] == "extra_forbidden"
        for error in exc_info.value.errors()
    )


def test_registration_parameters_take_every_type_with_typed_defaults():
    payload = _github_payload()
    payload["parameters"] = [
        {"key": "dry_run", "type": "boolean", "default": False, "tier_defaults": {"4": True}},
        {"key": "size", "type": "select", "options": ["small", "large"], "default": "small"},
        {"key": "replicas", "type": "number", "default": 2, "required": False},
    ]
    parameters = _CREATE_ADAPTER.validate_python(payload).parameters
    assert [(p.key, p.parameter_type.value, p.default) for p in parameters] == [
        ("dry_run", "boolean", False), ("size", "select", "small"), ("replicas", "number", 2)
    ]
    assert parameters[0].tier_defaults == {4: True}
    assert parameters[2].required is False


@pytest.mark.parametrize(
    ("parameter", "message"),
    [
        ({"key": "size", "type": "select"}, "needs at least one choice"),
        ({"key": "size", "type": "select", "options": ["s"], "default": "m"}, "one of its choices"),
        ({"key": "n", "type": "number", "default": "2"}, "must be a number"),
        ({"key": "flag", "type": "boolean", "tier_defaults": {"2": "yes"}}, "true or false"),
        ({"key": "t", "type": "text", "tier_defaults": {"5": "x"}}, "tiers 1 to 4"),
        ({"key": "t", "type": "text", "options": ["a"]}, "only supported for choice"),
    ],
)
def test_registration_parameter_values_must_fit_their_type(parameter, message):
    payload = _github_payload()
    payload["parameters"] = [parameter]
    with pytest.raises(ValidationError, match=message):
        _CREATE_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("field", ["endpoint", "api_token", "model_name"])
def test_github_registration_requires_complete_llm_configuration(field: str):
    payload = _github_payload()
    del payload["llm"][field]

    with pytest.raises(ValidationError):
        _CREATE_ADAPTER.validate_python(payload)


def test_github_registration_accepts_kubernetes_llm_endpoint():
    payload = _github_payload()
    payload["llm"]["endpoint"] = "amberd-llm-gateway:8010"

    registered = _CREATE_ADAPTER.validate_python(payload)

    assert registered.llm.endpoint == "amberd-llm-gateway:8010"


@pytest.mark.parametrize("port", [0, 65536])
def test_container_registration_rejects_invalid_ports(port: int):
    payload = _container_payload()
    payload["container"]["port"] = port

    with pytest.raises(ValidationError):
        _CREATE_ADAPTER.validate_python(payload)


def test_application_type_requires_its_matching_configuration():
    payload = _github_payload()
    payload["application_type"] = "containerized"

    with pytest.raises(ValidationError) as exc_info:
        _CREATE_ADAPTER.validate_python(payload)

    error_locations = [error["loc"] for error in exc_info.value.errors()]
    assert any(location[-1] == "container" for location in error_locations)
    assert any(location[-1] == "github" for location in error_locations)


def test_container_secret_value_is_write_only_and_absent_from_response_contract():
    secret = ContainerApplicationSecretCreate.model_validate(
        {
            "name": "chatbot-api-key",
            "environment_variable": "CHATBOT_API_KEY",
            "secret_value": "super-sensitive",
        }
    )

    assert secret.secret_value.get_secret_value() == "super-sensitive"
    assert "super-sensitive" not in repr(secret)
    assert "secret_value" not in ContainerApplicationSecretOut.model_fields


@pytest.mark.parametrize(
    ("name", "environment_variable"),
    [
        ("Uppercase", "API_KEY"),
        ("-leading", "API_KEY"),
        ("valid-name", "INVALID-NAME"),
        ("valid-name", "1INVALID"),
    ],
)
def test_container_secret_rejects_invalid_kubernetes_identifiers(
    name: str,
    environment_variable: str,
):
    with pytest.raises(ValidationError):
        ContainerApplicationSecretCreate(
            name=name,
            environment_variable=environment_variable,
            secret_value="value",
        )


def test_container_secret_update_requires_at_least_one_change():
    with pytest.raises(ValidationError, match="at least one secret field"):
        ContainerApplicationSecretUpdate()


@pytest.mark.parametrize("version", ["", "two words", "owner/image:2.0", "-invalid"])
def test_upgrade_requires_one_valid_version_or_git_ref(version: str):
    with pytest.raises(ValidationError):
        RegisteredApplicationDeploymentUpgrade(version=version)


def test_container_upgrade_strips_version_before_validation():
    payload = RegisteredApplicationDeploymentUpgrade(version=" 2.0.0 ")

    assert payload.version == "2.0.0"


def test_github_upgrade_accepts_a_branch_path():
    payload = RegisteredApplicationDeploymentUpgrade(version="release/2026.09")

    assert payload.version == "release/2026.09"


@pytest.mark.parametrize("confirmation", ["", "wrong value", "Uppercase", "-invalid"])
def test_container_termination_requires_valid_instance_name_confirmation(
    confirmation: str,
):
    with pytest.raises(ValidationError):
        RegisteredApplicationDeploymentTerminate(confirmation=confirmation)


def test_container_termination_strips_confirmation():
    payload = RegisteredApplicationDeploymentTerminate(confirmation=" chat-prod ")

    assert payload.confirmation == "chat-prod"


@pytest.mark.parametrize(
    "workflow_path",
    ["deploy.yml", "/.github/workflows/deploy.yml", ".github/workflows/../deploy.yml"],
)
def test_github_registration_rejects_invalid_workflow_paths(workflow_path: str):
    payload = _github_payload()
    payload["github"]["workflow_file_path"] = workflow_path

    with pytest.raises(ValidationError):
        _CREATE_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "repository",
            "https://github.com/amberd-ai",
            "Workflow repository 'https://github.com/amberd-ai' must use the "
            "owner/repository format",
        ),
        (
            "code_repository",
            "https://github.com/amberd-ai/athena",
            "Code repository 'https://github.com/amberd-ai/athena' must use the "
            "owner/repository format",
        ),
        (
            "workflow_file_path",
            "deploy.yml",
            "Workflow file 'deploy.yml' must be a YAML file under .github/workflows/",
        ),
    ],
)
def test_github_format_errors_name_the_field_and_the_typed_value(field, value, message):
    payload = _github_payload()
    payload["github"][field] = value

    with pytest.raises(ValidationError) as caught:
        _CREATE_ADAPTER.validate_python(payload)

    messages = [error["msg"] for error in caught.value.errors()]
    assert f"Value error, {message}" in messages
