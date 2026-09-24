"""Safe public contracts for reusable GitHub connections."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)


class _ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


def normalize_github_connection_url(value: str) -> str:
    """Validate and normalize a GitHub owner or repository web URL."""

    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("GitHub repository URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("GitHub repository URL cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("GitHub repository URL cannot contain a query or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("GitHub repository URL must use HTTPS")
    normalized_path = parsed.path.rstrip("/")
    repository_segments = [segment for segment in normalized_path.split("/") if segment]
    if len(repository_segments) not in {1, 2}:
        raise ValueError(
            "GitHub URL must identify an owner or an owner and repository"
        )
    if len(repository_segments) == 2 and repository_segments[1].endswith(".git"):
        repository_segments[1] = repository_segments[1][:-4]
    if not all(repository_segments):
        raise ValueError(
            "GitHub URL must identify an owner or an owner and repository"
        )
    normalized_path = "/" + "/".join(repository_segments)
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


class GitHubConnectionCredentials(_ContractModel):
    """Write-only credentials used to validate a GitHub owner or repository."""

    connection_name: str = Field(
        ...,
        validation_alias=AliasChoices("connection_name", "name"),
        min_length=1,
        max_length=200,
    )
    repository_url: str = Field(
        ...,
        validation_alias=AliasChoices("repository_url", "url", "connection_url"),
        min_length=1,
        max_length=2048,
    )
    access_token: SecretStr = Field(..., min_length=1, max_length=65536)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        return normalize_github_connection_url(value)


class GitHubConnectionCreate(GitHubConnectionCredentials):
    """Create a connection while keeping its access token write-only."""

    @property
    def connection_url(self) -> str:
        """Compatibility accessor for the persistence model's existing column name."""

        return self.repository_url


class GitHubConnectionValidationOut(_ContractModel):
    """Safe result returned after GitHub validates the supplied credentials."""

    valid: bool
    message: str
    repository: str | None = None


class GitHubConnectionOut(_ContractModel):
    """Connection metadata safe to return to browsers."""

    id: UUID | str
    display_name: str
    connection_url: str | None
    legacy: bool = False
    created_by: str | None = None
    created_at: datetime | None = None


class GitHubConnectionList(_ContractModel):
    items: list[GitHubConnectionOut]
    total: int = Field(..., ge=0)
