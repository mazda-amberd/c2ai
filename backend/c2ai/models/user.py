# pylint: disable=import-error
"""SQLAlchemy model for the `users` table."""

from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base

ADMIN = "admin"
USER = "user"
# How user types appear in the API (``metadata.user_type``) and the UI.
USER_TYPE_LABELS = {ADMIN: "Admin", USER: "User"}


def parse_user_type(value: object) -> str | None:
    """``"Admin"`` / ``"user"`` → the stored value; None when unrecognised."""

    normalised = str(value or "").strip().lower()
    return normalised if normalised in USER_TYPE_LABELS else None


class User(Base):
    """
    SQLAlchemy model for the `users` table.

    Attributes:
        id (UUID): Primary key.
        identifier (str): Unique identifier for the user.
        password (str): Hashed password.
        first_name (str): User's first name.
        last_name (str): User's last name.
        user_type (str): ``admin`` or ``user`` — the only source of admin rights.
        is_superuser (bool): Sees and manages every user (the bootstrap admin).
        metadata_ (dict): Profile metadata (job role, password-reset flag, ...).
        created_by (str): Identifier of the creator (audit label).
        created_by_id (UUID | None): The creating account.
        updated_by (str | None): Identifier of the last updater.
        createdAt (datetime): Timestamp of creation.
        updatedAt (datetime): Timestamp of last update.
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    identifier = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)

    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)

    user_type = Column(String, nullable=False, default=USER)
    is_superuser = Column(Boolean, nullable=False, default=False)

    metadata_ = Column("metadata", JSONB, nullable=False)

    created_by = Column(String, nullable=False)
    created_by_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by = Column(String, nullable=True)

    createdAt = Column("createdAt", DateTime(timezone=False), server_default=func.now())
    updatedAt = Column(
        "updatedAt",
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def is_admin(self) -> bool:
        return self.user_type == ADMIN

    @property
    def public_metadata(self) -> dict[str, Any]:
        """Metadata as the API has always shown it, with ``user_type`` from the column."""

        return {
            **(self.metadata_ or {}),
            "user_type": USER_TYPE_LABELS.get(self.user_type or USER, "User"),
        }

    def __repr__(self) -> str:
        """String representation of the User instance."""
        return f"<User id={self.id} identifier={self.identifier!r}>"

    def to_dict(self) -> dict:
        """Convert the User instance to a dictionary."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "metadata": self.public_metadata,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }
