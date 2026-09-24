# pylint: disable=import-error
"""
Custom exception classes for application error handling.

This module defines a hierarchy of exception classes for use throughout the application,
including HTTP error types and domain-specific errors. These exceptions provide
structured error information for consistent API responses and logging.
"""

from typing import Any, Optional


class AppException(Exception):
    """
    Base exception for application-specific errors.

    Attributes:
        detail: Error message or details.
        status_code: HTTP status code associated with the error.
        code: Optional error code string.
    """

    def __init__(self, detail: Any, status_code: int, code: Optional[str] = None):
        """
        Initialize AppException.

        Args:
            detail: Error message or details.
            status_code: HTTP status code.
            code: Optional error code string.
        """
        super().__init__(str(detail))
        self.detail = detail
        self.status_code = status_code
        self.code = code or self.__class__.__name__


class NotFoundError(AppException):
    """
    Exception for HTTP 404 Not Found errors.
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize NotFoundError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=404, code=code)


class ConflictError(AppException):
    """
    Exception for HTTP 409 Conflict errors.
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize ConflictError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=409, code=code)


class BadRequestError(AppException):
    """
    Exception for HTTP 400 Bad Request errors.
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize BadRequestError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=400, code=code)


class UnprocessableEntityError(AppException):
    """
    Exception for HTTP 422 Unprocessable Entity errors.
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize UnprocessableEntityError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=422, code=code)


class UnauthorizedError(AppException):
    """
    Exception for HTTP 401 Unauthorized errors.
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize UnauthorizedError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=401, code=code)


class ForbiddenError(AppException):
    """
    Exception for HTTP 403 Forbidden errors.
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize ForbiddenError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=403, code=code)


class DatabaseError(AppException):
    """
    Exception for database-related errors (HTTP 500).
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize DatabaseError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=500, code=code)


class ServiceUnavailableError(AppException):
    """
    Exception for service unavailable errors (HTTP 503).
    """

    def __init__(self, detail: Any, code: Optional[str] = None):
        """
        Initialize ServiceUnavailableError.

        Args:
            detail: Error message or details.
            code: Optional error code string.
        """
        super().__init__(detail=detail, status_code=503, code=code)


# ---------------- Domain-specific exceptions ----------------
# ---------------- User ----------------
class UserNotFound(NotFoundError):
    """
    Exception for user not found errors.
    """

    def __init__(self, identifier: str):
        """
        Initialize UserNotFound.

        Args:
            identifier: User identifier.
        """
        super().__init__(
            detail=f"User with identifier '{identifier}' not found",
            code="UserNotFound"
        )


class NoUsersFound(NotFoundError):
    """
    Exception for no users found errors.
    """

    def __init__(self):
        """
        Initialize NoUsersFound.
        """
        super().__init__(detail="No users were found.", code="NoUsersFound")



class DuplicateUser(ConflictError):
    """
    Exception for duplicate user errors.
    """

    def __init__(self, identifier: str):
        """
        Initialize DuplicateUser.

        Args:
            identifier: User identifier.
        """
        super().__init__(
            detail=f"User with identifier '{identifier}' already exists.",
            code="DuplicateUser"
        )


class OldPasswordIncorrect(BadRequestError):
    """
    Exception for incorrect old password errors.
    """

    def __init__(self):
        """
        Initialize OldPasswordIncorrect.
        """
        super().__init__(detail="Old password is incorrect", code="OldPasswordIncorrect")


class PasswordValidationFailed(UnprocessableEntityError):
    """
    Exception for password validation/configuration failures (HTTP 422).
    """

    def __init__(self, detail: Any):
        super().__init__(detail=detail, code="PasswordValidationFailed")


class ValidationFailed(UnprocessableEntityError):
    """
    Exception for validation failure errors.
    """

    def __init__(self, detail: Any):
        """
        Initialize ValidationFailed.

        Args:
            detail: Validation error details.
        """
        super().__init__(detail=detail, code="ValidationFailed")


class FailedToDelete(DatabaseError):
    """
    Exception for failed user deletion errors.
    """

    def __init__(self, identifier: str):
        """
        Initialize FailedToDelete.

        Args:
            identifier: User identifier.
        """
        super().__init__(
            detail=f"Failed to delete the user with name '{identifier}'",
            code="FailedToDelete"
        )


class CannotUpdateLastAdminToUser(ConflictError):
    """
    Exception for attempts to delete the last admin user.
    """

    def __init__(self):
        super().__init__(
            detail="Cannot change last 'Admin' user to 'User'.",
            code="CannotUpdateLastAdminToUser"
        )


class CannotDeleteLastAdminUser(ConflictError):
    """
    Exception for attempts to delete the last admin user.
    """

    def __init__(self):
        super().__init__(
            detail=(
                "You are the last Admin. To delete your account, "
                "please assign another Admin first."
            ),
            code="CannotDeleteLastAdminUser"
        )


def InvalidToken(detail: str = "Invalid token") -> AppException:
    """
    Factory for a 401 Invalid Token error.

    Returns:
        AppException: Unauthorized error with code `InvalidToken`.
    """
    return UnauthorizedError(detail=detail, code="InvalidToken")


def TokenExpired(detail: str = "Token expired") -> AppException:
    """
    Factory for a 401 Token Expired error.

    Returns:
        AppException: Unauthorized error with code `TokenExpired`.
    """
    return UnauthorizedError(detail=detail, code="TokenExpired")


class InvalidResourceName(ValidationFailed):
    """
    Generic invalid resource name error (empty or contains forbidden characters).

    Args:
        field: The name of the resource field (e.g., "bookmark name").
        detail: Specific detail about why the name is invalid.
    """

    def __init__(self, field: str, detail: str):
        super().__init__(detail=f"{field.capitalize()} is invalid: {detail}")


# ---------------- Grafana/Metrics ----------------
class GrafanaFetchError(AppException):
    """
    Exception for errors when calling Grafana (metrics or logs).
    """

    def __init__(self, detail: Any):
        """
        Initialize GrafanaFetchError.

        Args:
            detail: Error message or details about the fetch failure.
        """
        super().__init__(
            detail={"error": "Grafana request failed", "message": str(detail)},
            status_code=500,
            code="GrafanaFetchError",
        )


# ---------------- Registered Applications ----------------
class DuplicateRegisteredApplication(ConflictError):
    """Raised when an application name is already registered."""

    def __init__(self, name: str):
        super().__init__(
            detail=f"A registered application named '{name}' already exists.",
            code="DuplicateRegisteredApplication",
        )


class DuplicateGitHubConnection(ConflictError):
    """Raised when an active GitHub connection already uses the URL."""

    def __init__(self, connection_url: str):
        super().__init__(
            detail=f"A GitHub connection for '{connection_url}' already exists.",
            code="DuplicateGitHubConnection",
        )


class GitHubConnectionValidationFailed(UnprocessableEntityError):
    """Raised when GitHub rejects a repository connection's credentials."""

    def __init__(self, detail: str):
        super().__init__(
            detail=detail,
            code="GitHubConnectionValidationFailed",
        )


class RegisteredApplicationNotFound(NotFoundError):
    """Raised when a registered application is missing or already deleted."""

    def __init__(self, application_id: object):
        super().__init__(
            detail=f"Registered application '{application_id}' was not found.",
            code="RegisteredApplicationNotFound",
        )


class RegisteredApplicationHasRunningInstances(ConflictError):
    """Raised when deletion would orphan active deployment instances."""

    def __init__(self, name: str, instance_count: int):
        super().__init__(
            detail=(
                f"Registered application '{name}' cannot be deleted while it has "
                f"{instance_count} active deployment instance(s)."
            ),
            code="RegisteredApplicationHasRunningInstances",
        )


class RegisteredApplicationHasManagedSecrets(ConflictError):
    """Raised when deleting a template would orphan provider-held secrets."""

    def __init__(self, name: str, secret_count: int):
        super().__init__(
            detail=(
                f"Registered application '{name}' cannot be deleted while it has "
                f"{secret_count} managed secret(s). Delete those secrets first."
            ),
            code="RegisteredApplicationHasManagedSecrets",
        )


class DuplicateDeploymentInstance(ConflictError):
    """Raised when an instance name is already used in the selected Tier."""

    def __init__(self, name: str, tier: int):
        super().__init__(
            detail=(
                f"A deployment instance named '{name}' already exists in Tier {tier}."
            ),
            code="DuplicateDeploymentInstance",
        )


class DuplicateDeploymentSubdomain(ConflictError):
    """Raised when an active deployment already owns an Athena subdomain."""

    def __init__(self, subdomain: str):
        super().__init__(
            detail=f"The hostname '{subdomain}.amberd.ai' is already in use.",
            code="DuplicateDeploymentSubdomain",
        )


class ContainerSecretsNotSupported(UnprocessableEntityError):
    """Raised when managed secrets are requested for a non-container application."""

    def __init__(self):
        super().__init__(
            detail="Managed secrets are only supported for containerized applications.",
            code="ContainerSecretsNotSupported",
        )


class ContainerImageTagsNotSupported(UnprocessableEntityError):
    """Raised when registry tags are requested for a non-container application."""

    def __init__(self):
        super().__init__(
            detail="Image tags are only supported for containerized applications.",
            code="ContainerImageTagsNotSupported",
        )


class ContainerDeploymentNotSupported(UnprocessableEntityError):
    """Raised when the container deployment contract is used for another type."""

    def __init__(self):
        super().__init__(
            detail=(
                "This deployment endpoint is only supported for containerized "
                "applications."
            ),
            code="ContainerDeploymentNotSupported",
        )


class ContainerRegistryNotSupported(UnprocessableEntityError):
    """Raised when a registered container registry has no Athena adapter."""

    def __init__(self, registry: str):
        super().__init__(
            detail=f"Container registry '{registry}' is not supported.",
            code="ContainerRegistryNotSupported",
        )


class InvalidContainerImageRepository(UnprocessableEntityError):
    """Raised when a repository cannot be addressed by its registry adapter."""

    def __init__(self, repository: str, expected_format: str | None = None):
        expected = expected_format or "Docker Hub. Use the namespace/repository format."
        super().__init__(
            detail=(
                f"Container image repository '{repository}' is not valid for {expected}"
            ),
            code="InvalidContainerImageRepository",
        )


class ContainerImageTagNotFound(UnprocessableEntityError):
    """Raised when a requested image tag does not exist in the registry."""

    def __init__(self, repository: str, tag: str):
        super().__init__(
            detail=f"Image tag '{tag}' was not found for repository '{repository}'.",
            code="ContainerImageTagNotFound",
        )


class ContainerApplicationSecretNotFound(NotFoundError):
    """Raised when a managed container secret does not exist."""

    def __init__(self, secret_id: object):
        super().__init__(
            detail=f"Container application secret '{secret_id}' was not found.",
            code="ContainerApplicationSecretNotFound",
        )


class DuplicateContainerApplicationSecret(ConflictError):
    """Raised when a secret name or environment variable is already active."""

    def __init__(self):
        super().__init__(
            detail=(
                "A managed secret with this name or environment variable already "
                "exists for the application."
            ),
            code="DuplicateContainerApplicationSecret",
        )


class ContainerApplicationSecretInUse(ConflictError):
    """Raised when a deployment still depends on a managed secret."""

    def __init__(self, name: str, deployment_count: int):
        super().__init__(
            detail=(
                f"Managed secret '{name}' cannot be deleted while it is referenced "
                f"by {deployment_count} active or rollback-capable deployment(s)."
            ),
            code="ContainerApplicationSecretInUse",
        )


class DeploymentInstanceNotFound(NotFoundError):
    """Raised when a registered deployment instance does not exist."""

    def __init__(self, deployment_id: object):
        super().__init__(
            detail=f"Deployment instance '{deployment_id}' was not found.",
            code="DeploymentInstanceNotFound",
        )


class DeploymentProgressConflict(ConflictError):
    """Raised when a progress callback attempts an invalid transition."""

    def __init__(self, detail: str):
        super().__init__(detail=detail, code="DeploymentProgressConflict")


class DeploymentRollbackNotAvailable(ConflictError):
    """Raised when an instance cannot currently be rolled back."""

    def __init__(self, status: str):
        super().__init__(
            detail=f"Rollback is not available while deployment status is '{status}'.",
            code="DeploymentRollbackNotAvailable",
        )


class DeploymentUpgradeNotSupported(UnprocessableEntityError):
    """Raised when upgrade is requested for an unknown application type."""

    def __init__(self):
        super().__init__(
            detail="Upgrade is not supported for this deployment type.",
            code="DeploymentUpgradeNotSupported",
        )


class DeploymentUpgradeNotAvailable(ConflictError):
    """Raised when an instance is not in an upgradeable lifecycle state."""

    def __init__(self, status: str):
        super().__init__(
            detail=f"Upgrade is not available while deployment status is '{status}'.",
            code="DeploymentUpgradeNotAvailable",
        )


class DeploymentAlreadyAtVersion(ConflictError):
    """Raised when an upgrade would redeploy the currently configured image tag."""

    def __init__(self, version: str):
        super().__init__(
            detail=f"The deployment is already configured at version '{version}'.",
            code="DeploymentAlreadyAtVersion",
        )


class DeploymentTerminationNotSupported(UnprocessableEntityError):
    """Raised when termination is requested for an unknown application type."""

    def __init__(self):
        super().__init__(
            detail="Termination is not supported for this deployment type.",
            code="DeploymentTerminationNotSupported",
        )


class DeploymentTerminationNotAvailable(ConflictError):
    """Raised when an instance is not in a terminable lifecycle state."""

    def __init__(self, status: str):
        super().__init__(
            detail=f"Termination is not available while deployment status is '{status}'.",
            code="DeploymentTerminationNotAvailable",
        )


class DeploymentTerminationConfirmationMismatch(UnprocessableEntityError):
    """Raised when a destructive termination confirmation does not match."""

    def __init__(self):
        super().__init__(
            detail="Termination confirmation must match the deployment instance name.",
            code="DeploymentTerminationConfirmationMismatch",
        )


def InvalidCredentials(detail: str = "Username or password is incorrect") -> AppException:
    """Factory for a 401 Invalid Credentials error.

    Use this for login flows where you don't want to reveal whether the username
    or password was wrong.

    Returns:
        AppException: Unauthorized error with code `InvalidCredentials`.
    """
    return UnauthorizedError(detail=detail, code="InvalidCredentials")


def MissingToken(detail: str = "Missing token") -> AppException:
    """Factory for a 401 Missing Token error."""
    return UnauthorizedError(detail=detail, code="MissingToken")


def InvalidTokenExpirationFormat(
    detail: str = "Invalid token expiration format",
) -> AppException:
    """Factory for a 401 invalid token expiry format error."""
    return UnauthorizedError(detail=detail, code="InvalidTokenExpirationFormat")


def AdminPrivilegesRequired(detail: str = "Admin privileges required") -> AppException:
    """Factory for a 401 admin privileges required error."""
    return UnauthorizedError(detail=detail, code="AdminPrivilegesRequired")
