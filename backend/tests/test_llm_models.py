"""Tests for LLM model-name provider resolution and pricing messages."""

from c2ai.services.llm_models import (
    PUBLIC_MODEL_NAMES,
    is_private_model,
    is_supported_model,
    normalize_model_name,
    pricing_unavailable_message,
    resolve_llm_provider,
)


def test_resolves_private_and_supported_public_models():
    assert resolve_llm_provider("qwen3-coder-next") == "vllm"
    assert resolve_llm_provider("qwen3-6") == "vllm"
    assert resolve_llm_provider("claude-opus-5") == "anthropic"
    assert resolve_llm_provider("claude-haiku-4-5-20251001") == "anthropic"
    assert resolve_llm_provider("gpt-4.1-mini") == "openai"
    assert resolve_llm_provider("o3-mini") == "openai"
    assert resolve_llm_provider("gemini-2.0-flash-lite") == "gemini"
    assert resolve_llm_provider(" GPT-4o ") == "openai"


def test_reports_no_provider_for_an_unsupported_name():
    assert resolve_llm_provider("mistral-large") is None
    assert resolve_llm_provider("minstal") is None
    assert resolve_llm_provider("   ") is None
    # A name outside the list is unsupported even within a supported family.
    assert resolve_llm_provider("claude-sonnet-4-6") is None
    assert resolve_llm_provider("gemma-3") is None
    assert resolve_llm_provider("text-embedding-3-large") is None


def test_supported_models_are_the_private_and_listed_public_names():
    assert PUBLIC_MODEL_NAMES == (
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "o3",
        "o3-mini",
        "o4-mini",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    )
    assert all(is_supported_model(name) for name in PUBLIC_MODEL_NAMES)
    assert is_supported_model("qwen3-6") is True
    assert is_supported_model("minstal") is False


def test_normalizes_and_identifies_private_models():
    assert normalize_model_name("  gpt-4o  ") == "gpt-4o"
    assert is_private_model(" QWEN3-6 ") is True
    assert is_private_model("gpt-4o") is False


def test_pricing_message_names_the_model_and_its_routing():
    routed = pricing_unavailable_message("gemini-2.5-pro", "gemini")
    assert routed.startswith("Pricing not available for the model 'gemini-2.5-pro'.")
    assert "gemini" in routed

    unsupported = pricing_unavailable_message("mistral-large", None)
    assert unsupported.startswith(
        "Pricing not available for the model 'mistral-large'."
    )
    assert "C2AI supports only its listed models" in unsupported
