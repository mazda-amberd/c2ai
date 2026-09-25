"""User management as in Amberd Agents: the rules behind the Users page.

Four things are collected - first name, last name, email and a role of Admin
or User - and the email is the identity: it is what someone signs in with and
where their invitation goes. Every administrator sees and manages everyone.

* Adding someone generates a temporary password; only its hash is stored.
  It is emailed to them, and returned to the page only when the email did not
  go - shown once, because nothing can produce it again.
* Issuing a new temporary password stops the old one, and every session,
  immediately.
* The last Admin cannot be deleted or made a User, and nobody can delete the
  account they are signed in as.
"""

from __future__ import annotations

import html
import logging
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.clients.postmark import EmailDeliveryFailed, send_email
from c2ai.config import get_settings
from c2ai.core.exceptions import ConflictError, NotFoundError, ValidationFailed
from c2ai.crud import user as crud_user
from c2ai.models.user import ADMIN, USER, USER_TYPE_LABELS, User
from c2ai.services.password_reset import temporary_password

logger = logging.getLogger(__name__)

ROLES = (USER, ADMIN)
ROLE_LABELS = {role: USER_TYPE_LABELS[role] for role in ROLES}

INVITE_SUBJECT = "Your C2AI account"
RESET_SUBJECT = "Your new C2AI password"

# Deliberately loose. The authority on whether an address exists is whether
# the invitation arrives; a stricter pattern only rejects real addresses.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def display_name(user: User) -> str:
    return f"{user.first_name} {user.last_name}".strip()


def _clean(value: str, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValidationFailed(f"{field} is required.")
    if len(text) > 120:
        raise ValidationFailed(f"{field} is longer than 120 characters.")
    return text


def _clean_email(value: str) -> str:
    address = str(value or "").strip().lower()
    if not address:
        raise ValidationFailed(
            "An email address is required - it is what they sign in with, and "
            "where the invitation goes."
        )
    if len(address) > 320 or not _EMAIL.match(address):
        raise ValidationFailed(f"'{value}' does not look like an email address.")
    return address


def _clean_role(value: str) -> str:
    role = str(value or "").strip().lower()
    if role not in ROLES:
        raise ValidationFailed("Role must be one of: User, Admin.")
    return role


async def _person(db: AsyncSession, user_id: UUID) -> User:
    user = await crud_user.get_user_by_id(db, user_id)
    if user is None:
        raise NotFoundError("That person is no longer here.", code="UserNotFound")
    return user


async def _other_admins(db: AsyncSession, user: User) -> int:
    # Row-locked, so two requests cannot each remove "one of two" admins.
    return len([i for i in await crud_user.lock_admin_ids(db) if i != user.id])


async def list_people(db: AsyncSession) -> list[User]:
    """Admins first, then by name: the people who can change things, first."""

    users = (await db.execute(select(User))).scalars().all()
    return sorted(
        users,
        key=lambda u: (u.user_type != ADMIN, display_name(u).lower(), u.identifier),
    )


async def add_person(
    db: AsyncSession,
    *,
    first_name: str,
    last_name: str,
    email: str,
    role: str,
    added_by: str,
    added_by_id: UUID | None,
) -> tuple[User, str]:
    """Add someone; returns them with the temporary password they must be given."""

    first, last = _clean(first_name, "A first name"), _clean(last_name, "A last name")
    address, which = _clean_email(email), _clean_role(role)
    if await crud_user.get_user_for_sign_in(db, address) is not None:
        raise ConflictError(
            f"{address} is already here. An address identifies one person, because "
            f"it is what they sign in with.",
            code="DuplicateUser",
        )
    password = temporary_password()
    user = await crud_user.create_user(
        db,
        {
            "identifier": address,
            "password": password,
            "first_name": first,
            "last_name": last,
            "metadata_": {"user_type": which, "needs_password_reset": True},
            "created_by": added_by,
            "created_by_id": added_by_id,
        },
    )
    await db.commit()
    logger.info("User %s added as %s by %s", address, which, added_by)
    return user, password


async def update_person(
    db: AsyncSession,
    user_id: UUID,
    *,
    first_name: str,
    last_name: str,
    email: str,
    role: str,
    updated_by: str,
) -> User:
    user = await _person(db, user_id)
    first, last = _clean(first_name, "A first name"), _clean(last_name, "A last name")
    address, which = _clean_email(email), _clean_role(role)
    clash = await crud_user.get_user_for_sign_in(db, address)
    if clash is not None and clash.id != user.id:
        raise ConflictError(f"{address} is already someone else's address.", code="DuplicateUser")
    # The deployment must keep someone who can administer it.
    if user.user_type == ADMIN and which != ADMIN and await _other_admins(db, user) == 0:
        raise ConflictError(
            f"{display_name(user)} is the only Admin. Make someone else an Admin first, "
            f"or nobody can administer this deployment.",
            code="CannotUpdateLastAdminToUser",
        )
    await crud_user.update_user(
        db,
        user,
        {
            "identifier": address,
            "first_name": first,
            "last_name": last,
            "metadata_": {"user_type": which},
            "updated_by": updated_by,
        },
    )
    await db.commit()
    logger.info("User %s updated (%s) by %s", address, which, updated_by)
    return user


async def reset_person(db: AsyncSession, user_id: UUID, *, reset_by: str) -> tuple[User, str]:
    """Issue a new temporary password. The old one stops working immediately."""

    user = await _person(db, user_id)
    password = temporary_password()
    await crud_user.update_user_password(
        db, user, password, needs_password_reset=True, updated_by=reset_by
    )
    await db.commit()
    logger.info("Password reset for %s by %s", user.identifier, reset_by)
    return user, password


async def remove_person(
    db: AsyncSession, user_id: UUID, *, removed_by: str, removed_by_id: UUID | None
) -> User:
    user = await _person(db, user_id)
    if user.id == removed_by_id:
        raise ConflictError(
            "That is the account you are signed in as. Ask another Admin to remove it.",
            code="CannotDeleteYourself",
        )
    if user.user_type == ADMIN and await _other_admins(db, user) == 0:
        raise ConflictError(
            f"{display_name(user)} is the only Admin. Removing them would leave nobody "
            f"able to administer this deployment.",
            code="CannotDeleteLastAdminUser",
        )
    await crud_user.delete_user(db, user)
    await db.commit()
    logger.info("User %s removed by %s", user.identifier, removed_by)
    return user


def invitation(user: User, password: str, *, reset: bool = False) -> tuple[str, str]:
    """The email, as plain text and HTML.

    Says what the password is for and that it stops working - a temporary
    password that reads like a permanent one is one nobody changes.
    """

    where = get_settings().public_url.strip() or "your C2AI interface"
    opening = (
        "An administrator issued you a new temporary password for C2AI. Your "
        "previous password no longer works."
        if reset
        else "An account has been created for you on C2AI."
    )
    text = (
        f"Hello {user.first_name},\n\n{opening}\n\n"
        f"    Address:            {user.identifier}\n"
        f"    Temporary password: {password}\n\n"
        f"Sign in at {where}. You will be asked to choose your own password "
        f"straight away - this one works once and then stops.\n\n"
        f"If you were not expecting this, tell whoever administers your C2AI "
        f"deployment.\n"
    )
    body = (
        f"<p>Hello {html.escape(user.first_name)},</p><p>{opening}</p>"
        f"<table cellpadding='6' style='border-collapse:collapse;font-family:"
        f"ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px'>"
        f"<tr><td style='color:#6b7684'>Address</td>"
        f"<td><b>{html.escape(user.identifier)}</b></td></tr>"
        f"<tr><td style='color:#6b7684'>Temporary password</td>"
        f"<td><b>{password}</b></td></tr></table>"
        f"<p>Sign in at {html.escape(where)}. You will be asked to choose your own "
        f"password straight away &mdash; this one works once and then stops.</p>"
        f"<p style='color:#6b7684;font-size:13px'>If you were not expecting this, "
        f"tell whoever administers your C2AI deployment.</p>"
    )
    return text, body


async def send_invitation(user: User, password: str, *, reset: bool = False) -> str:
    """Email the temporary password. Returns why it did not go ("" when it did).

    Not configured is not a failure: the page is told, and shows the password
    once instead, because otherwise the account is one nobody can get into.
    """

    if not get_settings().email_enabled:
        return (
            "Email is not configured on this deployment "
            "(POSTMARK_SERVER_TOKEN and AMBERD_REPORT_FROM_EMAIL)."
        )
    text, body = invitation(user, password, reset=reset)
    try:
        await send_email(
            to=user.identifier,
            subject=RESET_SUBJECT if reset else INVITE_SUBJECT,
            text_body=text,
            html_body=body,
        )
    except EmailDeliveryFailed as error:
        logger.warning("Invitation to %s could not be sent: %s", user.identifier, error)
        return str(error).splitlines()[0]
    return ""
