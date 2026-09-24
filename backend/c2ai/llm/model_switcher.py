from __future__ import annotations

from c2ai.llm.config import Model, get_model_config
from c2ai.llm.vllm import create_vllm_chat_model


def create_runtime_chat_model(
    *,
    model_name: str | None,
):
    """
    Build a runtime chat model from a selected model name.
    """
    selected = (model_name or "").strip()
    config_ref = selected or Model.MODEL_CONFIG_NAME.value
    cfg = get_model_config(config_ref)

    return create_vllm_chat_model(cfg)


def extract_response_text(response) -> str:
    """Extract plain text from an LLM response.

    Handles standard string responses from vLLM.
    """
    content = response.content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "").strip()
        return ""
    return (content or "").strip()
