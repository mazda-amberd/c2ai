"""Email through Postmark's HTTP API (the same integration Amberd Agents uses).

The server token goes in an ``X-Postmark-Server-Token`` header; one message is
one recipient. Configured by ``POSTMARK_SERVER_TOKEN`` and ``C2AI_EMAIL_FROM``
(``Settings.email_enabled``); the sender must be a Sender Signature or on a
verified domain in Postmark, or Postmark refuses the send.
"""

from __future__ import annotations

import logging

import httpx

from c2ai.clients.http import get_http_client
from c2ai.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0


class EmailDeliveryFailed(RuntimeError):
    """Postmark could not be reached, or refused the message."""


async def send_email(
    *, to: str, subject: str, text_body: str, html_body: str | None = None
) -> str:
    """Send one message and return Postmark's MessageID."""

    settings = get_settings()
    if not settings.email_enabled:
        raise EmailDeliveryFailed(
            "Email is not configured (POSTMARK_SERVER_TOKEN and C2AI_EMAIL_FROM)."
        )
    payload = {
        "From": settings.email_from.strip(),
        "To": to,
        "Subject": subject,
        "TextBody": text_body,
        "MessageStream": settings.postmark_message_stream.strip() or "outbound",
    }
    if html_body:
        payload["HtmlBody"] = html_body
    if settings.email_reply_to.strip():
        payload["ReplyTo"] = settings.email_reply_to.strip()
    headers = {
        "Accept": "application/json",
        "X-Postmark-Server-Token": settings.postmark_server_token.strip(),
    }
    try:
        response = await get_http_client(_TIMEOUT).post(
            settings.postmark_endpoint, headers=headers, json=payload
        )
    except httpx.HTTPError as error:
        raise EmailDeliveryFailed(f"Postmark could not be reached: {error}") from error

    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = str(data.get("Message") or response.text or "").strip()
        raise EmailDeliveryFailed(
            f"Postmark refused the message (status {response.status_code}, "
            f"error code {data.get('ErrorCode')}): {message}"
        )
    message_id = str(data.get("MessageID") or "").strip()
    if not message_id:
        raise EmailDeliveryFailed("Postmark's answer had no MessageID.")
    logger.info("Postmark sent '%s' to %s (MessageID %s).", subject, to, message_id)
    return message_id
