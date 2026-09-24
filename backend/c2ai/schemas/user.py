"""Schemas for user models used in the Athena application."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, constr


class UserBase(BaseModel):
    """
    Base schema matching `src/models/db/user.py::User`.

    Attributes:
        identifier (str): Unique identifier for the user.
        first_name (str): User's first name.
        last_name (str): User's last name.
        metadata_ (Dict[str, Any]): Additional metadata for the user.
        created_by (str): Identifier of the creator.
        updated_by (Optional[str]): Identifier of the last updater.
    """

    identifier: constr(min_length=1)
    first_name: constr(min_length=1)
    last_name: constr(min_length=1)
    metadata_: dict[str, Any] = Field(default_factory=dict)
    created_by: constr(min_length=1)
    updated_by: str | None = None
    model_config = ConfigDict(from_attributes=True)

# pylint: disable=too-few-public-methods
class UserCreate(BaseModel):
    """
    Payload for creating a new user.

    Attributes:
        identifier (str): Unique identifier for the user.
        first_name (str): User's first name.
        last_name (str): User's last name.
        metadata_ (Dict[str, Any]): Additional metadata (user_type, role, etc.).
    """
    identifier: constr(min_length=1)
    first_name: constr(min_length=1)
    last_name: constr(min_length=1)
    metadata_: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(from_attributes=True)

# pylint: disable=too-few-public-methods
class UserUpdate(BaseModel):
    """
    Payload for updating an existing user.
    All fields are optional.

    Attributes:
        password (Optional[str]): Updated password.
        first_name (Optional[str]): Updated first name.
        last_name (Optional[str]): Updated last name.
        metadata_ (Optional[Dict[str, Any]]): Updated metadata.
        updated_by (Optional[str]): Identifier of the last updater.
    """

    password: constr(min_length=1) | None = None
    first_name: constr(min_length=1) | None = None
    last_name: constr(min_length=1) | None = None
    metadata_: dict[str, Any] | None = None
    updated_by: constr(min_length=1) | None = None
    model_config = ConfigDict(from_attributes=True)


class UpdatePasswordPayload(BaseModel):
    """
    Payload for updating a user's password.

    Attributes:
        new_password (str): The new password to set for the user.
    """
    new_password: constr(min_length=1)

# pylint: disable=too-few-public-methods
class UserOut(UserBase):
    """
    Response schema for a user (no password).

    Attributes:
        id (UUID): Unique identifier for the user.
        createdAt (datetime): Timestamp when the user was created.
        updatedAt (datetime): Timestamp when the user was last updated.
    """
    id: UUID
    createdAt: datetime
    updatedAt: datetime
    model_config = ConfigDict(from_attributes=True)


class UserOutWithCredentials(UserOut):
    """One-time credentials response after user creation.

    Attributes:
        temporary_password (str): The temporary password assigned to the user.
    """
    temporary_password: str


class UserUpdateResponse(BaseModel):
    """
    Response model for user update operations.

    Attributes:
        message (str): Confirmation message.
        user (UserOut): The updated user information.
    """
    message: str
    user: UserOut
    model_config = ConfigDict(from_attributes=True)


class UserPasswordUpdateResponse(BaseModel):
    """
    Response model for password reset/update operations.

    Attributes:
        message (str): Confirmation message.
        identifier (str): User identifier.
        temporary_password (str): The temporary password assigned to the user.
    """
    message: str
    identifier: str
    temporary_password: str
    model_config = ConfigDict(from_attributes=True)


class UserPasswordUpdate(BaseModel):
    """
    Generic response for password update operations.

    Attributes:
        message (str): Confirmation message.
    """
    message: str
    model_config = ConfigDict(from_attributes=True)
