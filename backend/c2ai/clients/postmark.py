"""Email through Postmark's HTTP API, configured exactly as in Amberd Agents.

The server token goes in an ``X-Postmark-Server-Token`` header; one message is
one recipient. Configured by ``POSTMARK_SERVER_TOKEN`` and
``AMBERD_REPORT_FROM_EMAIL`` (``Settings.email_enabled``); the sender must be
a Sender Signature or on a verified domain in Postmark, or Postmark refuses
the send. Optional: ``AMBERD_REPORT_REPLY_TO``, ``AMBERD_REPORT_MESSAGE_STREAM``,
``POSTMARK_EMAIL_ENDPOINT``, ``POSTMARK_CONNECT_TIMEOUT_SECONDS``,
``POSTMARK_READ_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import logging

import httpx

from c2ai.clients.http import get_http_client
from c2ai.config import get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryFailed(RuntimeError):
    """Postmark could not be reached, or refused the message."""


async def send_email(
    *, to: str, subject: str, text_body: str, html_body: str | None = None
) -> str:
    """Send one message and return Postmark's MessageID."""

    settings = get_settings()
    if not settings.email_enabled:
        raise EmailDeliveryFailed(
            "Email is not configured (POSTMARK_SERVER_TOKEN and AMBERD_REPORT_FROM_EMAIL)."
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
        response = await get_http_client().post(
            settings.postmark_endpoint,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(
                settings.postmark_read_timeout_seconds,
                connect=settings.postmark_connect_timeout_seconds,
            ),
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
