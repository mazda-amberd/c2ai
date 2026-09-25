"""Session revocation and login throttling tables (migration 0024)."""

from sqlalchemy import Column, DateTime, Integer, String

from .base import Base


class RevokedToken(Base):
    """A token signed out before it expired (logout)."""

    __tablename__ = "revoked_tokens"

    jti = Column(String, primary_key=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class LoginFailure(Base):
    """Failed sign-ins for one key (``account:<id>`` or ``client:<addr>``)."""

    __tablename__ = "login_failures"

    key = Column(String, primary_key=True)
    window_start = Column(DateTime(timezone=True), nullable=False)
    failures = Column(Integer, nullable=False)
