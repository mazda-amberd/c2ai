"""Typed application settings: the only place that reads the environment.

Every variable documented in ``.env.example`` is a field here. Values come from
the process environment first, then from ``backend/.env`` (override the file
with ``C2AI_ENV_FILE``; set it to an empty string to read no file, as the test
suite does).

``get_settings()`` is cached for the life of the process. The API validates
the settings once at startup (``check_startup_settings``) so a missing secret
or a malformed number fails the deploy instead of the first request.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_ENV_FILE = _BACKEND_DIR / ".env"


def _env(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        env_ignore_empty=True,
    )

    # --- Process ---------------------------------------------------------
    app_host: str = Field("0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(8007, validation_alias="APP_PORT")
    cors_origins: str = Field(
        "http://localhost:5173,http://127.0.0.1:5173", validation_alias="CORS_ORIGINS"
    )
    serve_frontend: bool = Field(True, validation_alias="C2AI_SERVE_FRONTEND")
    frontend_dist: str = Field("", validation_alias="C2AI_FRONTEND_DIST")
    run_worker: bool = Field(True, validation_alias="C2AI_RUN_WORKER")
    worker_poll_seconds: float = Field(1.0, gt=0, validation_alias="C2AI_WORKER_POLL_SECONDS")

    # --- Database --------------------------------------------------------
    database_url: str = Field("", validation_alias=_env("DATABASE_URL", "LOCAL_DATABASE_URL"))
    admin_password: str = Field("admin", validation_alias="C2AI_ADMIN_PASSWORD")

    # --- Authentication --------------------------------------------------
    auth_secret: str = Field("", validation_alias=_env("ATHENA_AUTH_SECRET", "JWT_SECRET"))
    auth_secret_length: int = Field(64, ge=32, validation_alias="ATHENA_AUTH_SECRET_LENGTH")
    token_ttl_seconds: int = Field(7 * 24 * 3600, validation_alias="ATHENA_TOKEN_TTL_SECONDS")
    token_max_ttl_seconds: int = Field(
        30 * 24 * 3600, validation_alias="ATHENA_TOKEN_MAX_TTL_SECONDS"
    )
    auth_cookie_name: str = Field("access_token", validation_alias="ATHENA_AUTH_COOKIE_NAME")
    cookie_samesite: str = Field("lax", validation_alias="ATHENA_COOKIE_SAMESITE")
    cookie_secure: bool | None = Field(None, validation_alias="ATHENA_COOKIE_SECURE")
    auth_cookie_domain: str = Field("", validation_alias="ATHENA_AUTH_COOKIE_DOMAIN")
    credential_encryption_key: str = Field(
        "", validation_alias="ATHENA_CREDENTIAL_ENCRYPTION_KEY"
    )
    # "kid:base64-32-byte-key,..." — the first key encrypts, all decrypt.
    encryption_keys: str = Field("", validation_alias="C2AI_ENCRYPTION_KEYS")
    login_max_failures_per_account: int = Field(
        5, ge=1, validation_alias="C2AI_LOGIN_MAX_FAILURES_PER_ACCOUNT"
    )
    login_max_failures_per_client: int = Field(
        20, ge=1, validation_alias="C2AI_LOGIN_MAX_FAILURES_PER_CLIENT"
    )
    login_failure_window_seconds: int = Field(
        900, ge=60, validation_alias="C2AI_LOGIN_FAILURE_WINDOW_SECONDS"
    )
    # Proxies whose X-Forwarded-For is trusted for the client address.
    forwarded_allow_ips: str = Field("127.0.0.1", validation_alias="C2AI_FORWARDED_ALLOW_IPS")

    # --- Passwords and identifiers ----------------------------------------
    password_prefix: str = Field("", validation_alias="PASSWORD_PREFIX")
    password_specials: str = Field("!@#$%^&*()_+-", validation_alias="PASSWORD_SPECIALS")
    password_length: int = Field(10, validation_alias="PASSWORD_LENGTH")
    identifier_min_length: int = Field(3, validation_alias="IDENTIFIER_MIN_LENGTH")
    identifier_max_length: int = Field(30, validation_alias="IDENTIFIER_MAX_LENGTH")
    identifier_allowed_symbols: str = Field("@_+.", validation_alias="IDENTIFIER_ALLOWED_SYMBOLS")

    # --- GitHub and pipelines ---------------------------------------------
    github_pat: str = Field("", validation_alias=_env("GITHUB_PAT", "GITHUB_TOKEN"))
    github_repo_owner: str = Field("amberd-ai", validation_alias="GITHUB_REPO_OWNER")
    github_repo_name: str = Field("devops", validation_alias="GITHUB_REPO_NAME")
    devops_branch: str = Field("main", validation_alias="DEVOPS_BRANCH")
    deploy_source_repo: str = Field("dealership_new", validation_alias="DEPLOY_SOURCE_REPO")
    slack_user_default_repo_owners: str = Field(
        "amberd-ai", validation_alias="SLACK_USER_DEFAULT_REPO_OWNERS"
    )
    container_deployment_repository: str = Field(
        "amberd-ai/devops", validation_alias="CONTAINER_DEPLOYMENT_REPOSITORY"
    )
    container_deployment_workflow: str = Field(
        "containerized-app-deploy.yaml", validation_alias="CONTAINER_DEPLOYMENT_WORKFLOW"
    )
    container_deployment_event_type: str = Field(
        "containerized-deploy", validation_alias="CONTAINER_DEPLOYMENT_EVENT_TYPE"
    )
    container_upgrade_repository: str = Field(
        "amberd-ai/devops", validation_alias="CONTAINER_UPGRADE_REPOSITORY"
    )
    container_upgrade_workflow: str = Field(
        "containerized-app-update.yaml", validation_alias="CONTAINER_UPGRADE_WORKFLOW"
    )
    container_termination_repository: str = Field(
        "amberd-ai/devops", validation_alias="CONTAINER_TERMINATION_REPOSITORY"
    )
    container_termination_workflow: str = Field(
        "containerized-app-terminate.yaml", validation_alias="CONTAINER_TERMINATION_WORKFLOW"
    )
    callback_base_url: str = Field(
        "https://athena.amberd.ai", validation_alias="ATHENA_CALLBACK_BASE_URL"
    )
    deployment_callback_token: str = Field("", validation_alias="DEPLOYMENT_CALLBACK_TOKEN")
    callback_accept_shared_token: bool = Field(
        True, validation_alias="C2AI_CALLBACK_ACCEPT_SHARED_TOKEN"
    )
    # Send callback_token to the container update/terminate workflows. They
    # use workflow_dispatch, which rejects inputs a workflow does not declare,
    # so enable this once the workflows declare it.
    container_workflows_accept_callback_token: bool = Field(
        False, validation_alias="CONTAINER_WORKFLOWS_ACCEPT_CALLBACK_TOKEN"
    )
    instance_domain: str = Field("amberd.ai", validation_alias="ATHENA_INSTANCE_DOMAIN")

    # --- Container registries and secrets ---------------------------------
    docker_hub_api_url: str = Field("", validation_alias="DOCKER_HUB_API_URL")
    registry_credential_provider_url: str = Field(
        "", validation_alias="REGISTRY_CREDENTIAL_PROVIDER_URL"
    )
    registry_credential_provider_token: str = Field(
        "", validation_alias="REGISTRY_CREDENTIAL_PROVIDER_TOKEN"
    )
    container_secret_provider_url: str = Field(
        "", validation_alias="CONTAINER_SECRET_PROVIDER_URL"
    )
    container_secret_provider_token: str = Field(
        "", validation_alias="CONTAINER_SECRET_PROVIDER_TOKEN"
    )

    # --- Grafana / Prometheus / Loki --------------------------------------
    grafana_api_url: str = Field("", validation_alias="GRAFANA_API_URL")
    grafana_api_token: str = Field("", validation_alias="GRAFANA_API_TOKEN")
    grafana_prometheus_datasource_uid: str = Field(
        "prometheus", validation_alias="GRAFANA_PROMETHEUS_DATASOURCE_UID"
    )
    grafana_loki_datasource_uid: str = Field("", validation_alias="GRAFANA_LOKI_DATASOURCE_UID")
    grafana_loki_max_lines: int = Field(
        2000, ge=1, le=50000, validation_alias="GRAFANA_LOKI_MAX_LINES"
    )
    grafana_loki_namespace_label: str = Field(
        "namespace", validation_alias="GRAFANA_LOKI_NAMESPACE_LABEL"
    )
    grafana_loki_deployment_label: str = Field(
        "deployment", validation_alias="GRAFANA_LOKI_DEPLOYMENT_LABEL"
    )
    grafana_loki_tier_label: str = Field("", validation_alias="GRAFANA_LOKI_TIER_LABEL")
    tier1_label_regex: str = Field("tier1", validation_alias="ATHENA_TIER1_LABEL_REGEX")
    tier2_label_regex: str = Field("tier2", validation_alias="ATHENA_TIER2_LABEL_REGEX")
    tier3_label_regex: str = Field("tier3|prod", validation_alias="ATHENA_TIER3_LABEL_REGEX")
    tier1_gpu_cluster: str = Field("qwen-5254d", validation_alias="ATHENA_TIER1_GPU_CLUSTER")
    tier2_gpu_cluster: str = Field("qwen-pq9sc", validation_alias="ATHENA_TIER2_GPU_CLUSTER")
    tier3_gpu_cluster: str = Field("qwen-l8dnl", validation_alias="ATHENA_TIER3_GPU_CLUSTER")
    excluded_deployments: str = Field("nginx", validation_alias="ATHENA_EXCLUDED_DEPLOYMENTS")
    cpu_cores_cap: float = Field(8.0, ge=1, validation_alias="ATHENA_CPU_CORES_CAP")
    memory_gb_cap: float = Field(80.0, ge=1, validation_alias="ATHENA_MEMORY_GB_CAP")
    inventory_refresh_seconds: int = Field(
        60, ge=10, validation_alias="C2AI_INVENTORY_REFRESH_SECONDS"
    )
    scrape_interval_seconds: int = Field(
        60, ge=1, validation_alias="ATHENA_SCRAPE_INTERVAL_SECONDS"
    )

    # --- Cost tracking ----------------------------------------------------
    financial_ingestion_enabled: bool = Field(
        True, validation_alias="ATHENA_FINANCIAL_INGESTION_ENABLED"
    )
    financial_poll_seconds: int = Field(
        3600, ge=1, validation_alias="ATHENA_FINANCIAL_POLL_SECONDS"
    )
    financial_min_window_seconds: int = Field(
        900, ge=1, validation_alias="ATHENA_FINANCIAL_MIN_WINDOW_SECONDS"
    )

    # --- LLM --------------------------------------------------------------
    vllm_endpoint: str = Field("", validation_alias="VLLM_ENDPOINT")
    vllm_qwen3_6_endpoint: str = Field("", validation_alias="VLLM_QWEN3_6_ENDPOINT")
    vllm_embedding_endpoint: str = Field("", validation_alias="VLLM_EMBEDDING_ENDPOINT")
    vllm_api_key: str = Field("", validation_alias="VLLM_API_KEY")

    @field_validator("cookie_secure", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("cookie_samesite")
    @classmethod
    def _samesite(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"lax", "strict", "none"}:
            raise ValueError("must be lax, strict or none")
        return value

    # --- Derived values ---------------------------------------------------

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cookie_is_secure(self) -> bool:
        # Browsers reject SameSite=None cookies that are not Secure.
        if self.cookie_samesite == "none":
            return True
        return bool(self.cookie_secure)

    @property
    def slack_user_default_owner_set(self) -> set[str]:
        return {
            owner.strip().lower()
            for owner in self.slack_user_default_repo_owners.split(",")
            if owner.strip()
        }

    @property
    def excluded_deployment_set(self) -> frozenset[str]:
        return frozenset(
            name.strip().lower() for name in self.excluded_deployments.split(",") if name.strip()
        )

    def tier_label_regex(self, tier_key: str) -> str:
        """``Tier1`` → ``ATHENA_TIER1_LABEL_REGEX`` (``^$`` when blank or unknown)."""
        value = getattr(self, f"{tier_key.lower()}_label_regex", "")
        return value.strip() or "^$"

    def tier_gpu_cluster(self, tier_key: str) -> str:
        return getattr(self, f"{tier_key.lower()}_gpu_cluster", "").strip()


def _env_file() -> Path | None:
    configured = os.environ.get("C2AI_ENV_FILE")
    if configured is None:
        return _DEFAULT_ENV_FILE if _DEFAULT_ENV_FILE.exists() else None
    return Path(configured).expanduser() if configured.strip() else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings (tests clear the cache when they change env)."""

    return Settings(_env_file=_env_file())


def check_startup_settings(settings: Settings | None = None) -> None:
    """Fail fast on configuration the API cannot run without."""

    settings = settings or get_settings()
    missing = [
        name
        for name, value in (
            ("DATABASE_URL", settings.database_url),
            ("ATHENA_AUTH_SECRET", settings.auth_secret),
        )
        if not value.strip()
    ]
    if settings.encryption_keys.strip():
        from c2ai.security.crypto import parse_keys

        try:
            parse_keys(settings.encryption_keys)
        except ValueError as error:
            raise RuntimeError(f"C2AI_ENCRYPTION_KEYS: {error}") from error
    if missing:
        raise RuntimeError(
            "Missing required configuration: " + ", ".join(missing) + ". See backend/.env.example."
        )
