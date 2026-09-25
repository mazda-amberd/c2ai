"""
Configure colored logging for the 'service' logger.
"""

import logging
import sys


class ColorFormatter(logging.Formatter):
    """
    Custom formatter to add colors to log levels.

    Uses ANSI escape codes for coloring.
    """
    RESET = "\033[0m"
    COLORS = {
        logging.DEBUG: "\033[36m",     # cyan
        logging.INFO: "\033[32m",      # green
        logging.WARNING: "\033[33m",   # yellow
        logging.ERROR: "\033[31m",     # red
        logging.CRITICAL: "\033[1;31m" # bright/bold red
    }

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the log record with color based on its level.

        Args:
            record (logging.LogRecord): The log record to format.

        Returns:
            str: The formatted log message with color.
        """
        # Apply color based on the log level of the record being formatted
        color = self.COLORS.get(record.levelno, "")

        # Avoid permanently mutating the record; restore after formatting
        had_attr = hasattr(record, "colored_level")
        old_val = getattr(record, "colored_level", None)
        try:
            record.colored_level = f"{color}{record.levelname}{self.RESET}" if color else record.levelname
            return super().format(record)
        finally:
            if had_attr:
                record.colored_level = old_val
            else:
                try:
                    delattr(record, "colored_level")
                except AttributeError:
                    pass


def _make_colored_handler() -> logging.Handler:
    from c2ai.config import get_settings
    from c2ai.core.observability import CorrelationIdFilter, JsonFormatter

    handler = logging.StreamHandler(stream=sys.stdout)
    handler._sql_chat_colored = True  # sentinel to avoid duplicates
    handler.addFilter(CorrelationIdFilter())
    if get_settings().log_format == "json":
        handler.setFormatter(JsonFormatter())
        return handler
    # [request or job id] on every line ties together what one request did.
    fmt = "%(asctime)s - %(colored_level)s [%(correlation_id)s]: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handler.setFormatter(ColorFormatter(fmt=fmt, datefmt=datefmt))
    return handler


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure colored console logging.

    Important: most modules log with logging.getLogger(__name__) or uvicorn.*, so
    we configure the root logger to ensure formatting applies globally.

    Safe to call multiple times.
    """
    handler = _make_colored_handler()

    # 1) Configure root so all module loggers inherit it.
    root = logging.getLogger()
    if not any(getattr(h, "_sql_chat_colored", False) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(level)

    # 2) Keep the original 'service' logger working too.
    service_logger = logging.getLogger("service")
    if not any(getattr(h, "_sql_chat_colored", False) for h in service_logger.handlers):
        service_logger.addHandler(handler)
    service_logger.setLevel(level)
    service_logger.propagate = True

    # 3) Uvicorn uses its own loggers/handlers by default.
    # We keep them quiet so only your application's logs show.
    # - access logger: request lines (127.0.0.1 - "GET /...")
    # - error logger: startup/shutdown and some warnings
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers[:] = []
    uvicorn_access.propagate = False
    uvicorn_access.disabled = True

    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.handlers[:] = []
    uvicorn_error.propagate = False
    uvicorn_error.setLevel(max(level, logging.WARNING))

    uvicorn_root = logging.getLogger("uvicorn")
    uvicorn_root.handlers[:] = []
    uvicorn_root.propagate = False
    uvicorn_root.setLevel(max(level, logging.WARNING))

    # 4) Silence httpx/httpcore INFO "HTTP Request: POST ..." (only when query succeeds).
    #    Other app INFO logs stay intact; httpx errors still show at WARNING+.
    for logger_name in ("httpx", "httpcore"):
        lg = logging.getLogger(logger_name)
        lg.setLevel(logging.WARNING)


def disable_sqlalchemy_logs() -> None:
    # Aggressively silence SQLAlchemy (including the Engine logger that emits SQL)
    for name in (
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        "sqlalchemy.pool",
    ):
        lg = logging.getLogger(name)
        lg.handlers[:] = []          # remove any handlers
        lg.propagate = False         # don't bubble to root
        lg.disabled = True           # hard disable
        lg.setLevel(logging.CRITICAL)

    # Root-level safety net (drops anything that slips through)
    class _DropSqlalchemyFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return not record.name.startswith("sqlalchemy")
    root = logging.getLogger()
    if not any(isinstance(f, _DropSqlalchemyFilter) for f in root.filters):
        root.addFilter(_DropSqlalchemyFilter())
