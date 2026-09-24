"""Fire-and-forget asyncio tasks that are kept alive until they finish.

The event loop only holds weak references to tasks, so a bare
``asyncio.create_task(...)`` whose result is discarded can be garbage
collected before it completes. Every background task is therefore kept in a
module-level set and removed once done; shutdown cancels whatever is left.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def _on_done(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "Background task %s failed",
            task.get_name(),
            exc_info=(type(error), error, error.__traceback__),
        )


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Schedule ``coro`` and hold a strong reference until it completes."""

    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


async def cancel_background_tasks() -> None:
    """Cancel outstanding tasks during application shutdown."""

    pending = list(_tasks)
    for task in pending:
        task.cancel()
    for task in pending:
        with suppress(asyncio.CancelledError, Exception):
            await task
