"""Shared runtime provider for the configured troubleshooting LLM."""

import logging
from typing import Any

from c2ai.core.exceptions import ServiceUnavailableError
from c2ai.llm.config import Model
from c2ai.llm.model_switcher import create_runtime_chat_model

logger = logging.getLogger(__name__)

_qa_llm: Any | None = None


def get_qa_llm() -> Any:
    """Create the configured vLLM client once and reuse it across reports."""

    global _qa_llm
    if _qa_llm is None:
        try:
            _qa_llm = create_runtime_chat_model(
                model_name=Model.MODEL_CONFIG_NAME.value,
            )
        except Exception as exc:
            logger.exception("Could not initialize the vLLM client: %s", exc)
            raise ServiceUnavailableError(
                "The language model service is not configured or unavailable.",
                code="LLMServiceUnavailable",
            ) from exc
    return _qa_llm
