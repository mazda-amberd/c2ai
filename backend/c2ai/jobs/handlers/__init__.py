"""Job handlers; importing this package registers every kind with the worker."""

from c2ai.jobs.handlers import (  # noqa: F401
    credentials,
    deployments,
    financial,
    maintenance,
    troubleshooting,
)
