# pylint: disable=import-error
"""SQLAlchemy model for the `users` table created by `src/init_db.py`."""

from uuid import uuid4

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base


class User(Base):
    """
    SQLAlchemy model for the `users` table.

    Attributes:
        id (UUID): Primary key.
        identifier (str): Unique identifier for the user.
        password (str): Hashed password.
        first_name (str): User's first name.
        last_name (str): User's last name.
        metadata_ (dict): Additional metadata for the user.
        created_by (str): Identifier of the creator.
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

    metadata_ = Column("metadata", JSONB, nullable=False)

    created_by = Column(String, nullable=False)
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
        """Check if the user has admin privileges based on metadata."""
        data = self.metadata_ or {}
        return str(data.get("user_type", "")).lower() == "admin"

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
            "metadata": self.metadata_,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }
