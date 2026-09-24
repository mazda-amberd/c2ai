"""Tests for the shared troubleshooting LLM provider."""

import pytest

from c2ai.llm import provider as llm_provider
from c2ai.core.exceptions import ServiceUnavailableError


def test_provider_creates_and_reuses_one_model(monkeypatch):
    model = object()

    def create_model(**_kwargs):
        return model

    monkeypatch.setattr(llm_provider, "_qa_llm", None)
    monkeypatch.setattr(llm_provider, "create_runtime_chat_model", create_model)

    assert llm_provider.get_qa_llm() is model
    assert llm_provider.get_qa_llm() is model


def test_provider_maps_configuration_failure(monkeypatch):
    def fail_to_create_model(*, model_name):
        _ = model_name
        raise ValueError("VLLM_ENDPOINT is missing")

    monkeypatch.setattr(llm_provider, "_qa_llm", None)
    monkeypatch.setattr(
        llm_provider,
        "create_runtime_chat_model",
        fail_to_create_model,
    )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        llm_provider.get_qa_llm()

    assert exc_info.value.code == "LLMServiceUnavailable"
