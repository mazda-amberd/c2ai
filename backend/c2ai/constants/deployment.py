"""Deployment domain constants shared by API and CRUD layers."""

from __future__ import annotations

# Terminal outcomes accepted by the inbound GitHub Actions completion webhook.
WEBHOOK_TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled"})
