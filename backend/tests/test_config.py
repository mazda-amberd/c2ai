"""Typed settings: aliases, blank values, validation, and the startup check."""

import pytest
from pydantic import ValidationError

from c2ai.config import Settings, check_startup_settings, get_settings


def _settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


def test_defaults_match_documented_values(monkeypatch):
    for name in ("GITHUB_REPO_OWNER", "DEVOPS_BRANCH", "ATHENA_TOKEN_TTL_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    settings = get_settings()
    assert settings.github_repo_owner == "amberd-ai"
    assert settings.devops_branch == "main"
    assert settings.token_ttl_seconds == 7 * 24 * 3600
    assert settings.tier_label_regex("Tier3") == "tier3|prod"


def test_legacy_aliases_are_accepted(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LOCAL_DATABASE_URL", "postgresql://legacy/db")
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_legacy")
    settings = get_settings()
    assert settings.database_url == "postgresql://legacy/db"
    assert settings.github_pat == "ghp_legacy"


def test_blank_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("DEVOPS_BRANCH", "")
    monkeypatch.setenv("ATHENA_TOKEN_TTL_SECONDS", "")
    settings = get_settings()
    assert settings.devops_branch == "main"
    assert settings.token_ttl_seconds == 7 * 24 * 3600


def test_malformed_numbers_fail_instead_of_silently_defaulting(monkeypatch):
    monkeypatch.setenv("ATHENA_TOKEN_TTL_SECONDS", "a week")
    with pytest.raises(ValidationError):
        get_settings()


def test_samesite_none_forces_secure_cookie(monkeypatch):
    monkeypatch.setenv("ATHENA_COOKIE_SAMESITE", "None")
    monkeypatch.setenv("ATHENA_COOKIE_SECURE", "false")
    settings = get_settings()
    assert settings.cookie_samesite == "none"
    assert settings.cookie_is_secure is True


def test_invalid_samesite_is_rejected(monkeypatch):
    monkeypatch.setenv("ATHENA_COOKIE_SAMESITE", "sometimes")
    with pytest.raises(ValidationError):
        get_settings()


def test_env_file_is_read_and_environment_wins(tmp_path, monkeypatch):
    env_file = tmp_path / "c2ai.env"
    env_file.write_text("DEVOPS_BRANCH=from-file\nGITHUB_REPO_NAME=file-repo\n")
    monkeypatch.setenv("C2AI_ENV_FILE", str(env_file))
    monkeypatch.setenv("GITHUB_REPO_NAME", "env-repo")
    monkeypatch.delenv("DEVOPS_BRANCH", raising=False)
    settings = get_settings()
    assert settings.devops_branch == "from-file"
    assert settings.github_repo_name == "env-repo"


def test_startup_check_names_every_missing_setting():
    with pytest.raises(RuntimeError, match="DATABASE_URL, ATHENA_AUTH_SECRET"):
        check_startup_settings(_settings(DATABASE_URL="", ATHENA_AUTH_SECRET=""))
    check_startup_settings(_settings(DATABASE_URL="postgresql://x/y", ATHENA_AUTH_SECRET="s"))
