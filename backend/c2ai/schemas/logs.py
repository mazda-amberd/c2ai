"""Pydantic schemas for deployment log endpoints (Grafana Loki)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LogLevelLiteral = Literal["debug", "trace", "info", "warning", "error", "fatal", "critical"]


class DeploymentLogEntryOut(BaseModel):
    """One log line normalised for the Athena log viewer."""

    id: str
    timestamp: str
    level: LogLevelLiteral
    logType: str
    user: str
    db: str
    app: str
    client: str
    role: str
    durationMs: int | None = None
    connectionId: str
    message: str
    labels: dict[str, str] = Field(default_factory=dict)


class DeploymentLogsResponseOut(BaseModel):
    entries: list[DeploymentLogEntryOut] = Field(default_factory=list)
    source: Literal["loki"] = "loki"
    has_more: bool = False
    next_cursor: str | None = None
