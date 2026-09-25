"""Forgot password: email a new temporary password (Amberd Agents' behaviour).

``POST /auth/forgot-password`` only queues this; the reset runs in the
``auth.password_reset`` job, so the endpoint answers the same way, and in the
same time, whether or not the address belongs to anyone. What actually
happened is logged, never returned.

* An unknown address, one inside the five-minute cooldown, or an account
  whose username is not an email address: nothing happens.
* Email not configured: nothing happens. A reset that cannot be delivered
  locks somebody out of an account that was working.
* Otherwise the new password replaces the old one (every session of the
  account ends) and the person must choose their own at the next sign-in.
  The change is committed only once Postmark has accepted the email, so a
  failed send leaves the old password working.
"""

from __future__ import annotations

import html
import logging
import re
import secrets
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.clients.postmark import EmailDeliveryFailed, send_email
from c2ai.config import get_settings
from c2ai.crud import session as session_store, user as crud_user
from c2ai.models.user import User

logger = logging.getLogger(__name__)

#: Long enough that the form is not a way to keep an account locked out,
#: short enough that a genuine second attempt is not a wait. Counted in the
#: login_failures table, so every replica enforces it.
RESET_COOLDOWN = timedelta(minutes=5)

ANSWER = (
    "If that address belongs to an account here, a temporary password is on its "
    "way. Check your email, then sign in with it - you will be asked to choose "
    "your own."
)

SUBJECT = "Your C2AI password"

#: No 0/O/1/l/I. A temporary password is read off an email and typed by hand,
#: and an ambiguous character there is a support call.
_ALPHABET = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXY23456789"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def temporary_password() -> str:
    """Three groups of four. Long enough to be a password, short enough to type."""

    body = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return f"{body[:4]}-{body[4:8]}-{body[8:]}"


def cooldown_key(identifier: str) -> str:
    return f"reset:{identifier.strip().lower()}"


def reset_body(user: User, password: str) -> tuple[str, str]:
    """The email, as plain text and HTML."""

    where = get_settings().public_url.strip() or "your C2AI interface"
    text = (
        f"Hello {user.first_name},\n\n"
        f"A new password was requested for your C2AI account. Your previous "
        f"password no longer works.\n\n"
        f"    Address:            {user.identifier}\n"
        f"    Temporary password: {password}\n\n"
        f"Sign in at {where}. You will be asked to choose your own password "
        f"straight away.\n\n"
        f"If you did not ask for this, someone entered your address on the "
        f"sign-in page. Nobody has seen this password but you; sign in and "
        f"choose a new one, and tell whoever administers your C2AI deployment.\n"
    )
    name, address, place = (
        html.escape(user.first_name),
        html.escape(user.identifier),
        html.escape(where),
    )
    body = (
        f"<p>Hello {name},</p>"
        f"<p>A new password was requested for your C2AI account. "
        f"Your previous password no longer works.</p>"
        f"<table cellpadding='6' style='border-collapse:collapse;font-family:"
        f"ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px'>"
        f"<tr><td style='color:#6b7684'>Address</td><td><b>{address}</b></td></tr>"
        f"<tr><td style='color:#6b7684'>Temporary password</td>"
        f"<td><b>{password}</b></td></tr></table>"
        f"<p>Sign in at {place}. You will be asked to choose your own password "
        f"straight away.</p>"
        f"<p style='color:#6b7684;font-size:13px'>If you did not ask for this, "
        f"someone entered your address on the sign-in page. Nobody has seen "
        f"this password but you; sign in, choose a new one, and tell whoever "
        f"administers your C2AI deployment.</p>"
    )
    return text, body


async def request_password_reset(db: AsyncSession, address: str) -> str:
    """Email a new temporary password to ``address`` if it is one of ours.

    Returns what happened, for the log only - the caller must not tell the
    person who asked, or the form confirms which addresses exist.
    """

    user = await crud_user.get_user_by_identifier(db, address.strip())
    if user is None:
        return "unknown address"
    identifier = user.identifier
    if not _EMAIL.fullmatch(identifier):
        return "the account's username is not an email address"
    key = cooldown_key(identifier)
    if await session_store.failures_in_window(db, [key], RESET_COOLDOWN):
        return "still within the cooldown"
    if not get_settings().email_enabled:
        return "email is not configured"

    password = temporary_password()
    await crud_user.update_user_password(
        db, user, password, needs_password_reset=True, updated_by="forgot-password"
    )
    text, body = reset_body(user, password)
    try:
        await send_email(to=identifier, subject=SUBJECT, text_body=text, html_body=body)
    except EmailDeliveryFailed as error:
        await db.rollback()
        logger.warning("Password reset for %s could not be sent: %s", identifier, error)
        return f"send failed: {str(error).splitlines()[0]}"
    await session_store.record_failure(db, [key], RESET_COOLDOWN)
    await db.commit()
    return "sent"
