"""
Model-name resolution for LLM configurations.

Athena supports a fixed set of model names: the private models served in the
cluster, plus the public models the gateway routes. A registration may still
carry any name, so the question answered here is whether a supplied name is one
Athena knows — an unsupported name is reported up front, because its usage
cannot be costed.
"""

from __future__ import annotations

# Models served by the in-cluster vLLM deployment. Their usage is billed from
# the private GPU-hour rate rather than a published per-token rate.
PRIVATE_PROVIDER = "vllm"
PRIVATE_MODEL_NAMES: tuple[str, ...] = ("qwen3-coder-next", "qwen3-6")

# Public models the gateway routes, in the order they are suggested. A name
# outside this mapping is not supported, so its usage cannot be costed.
PUBLIC_MODEL_PROVIDERS: dict[str, str] = {
    "claude-opus-5": "anthropic",
    "claude-sonnet-5": "anthropic",
    "claude-haiku-4-5-20251001": "anthropic",
    "gpt-4.1": "openai",
    "gpt-4.1-mini": "openai",
    "gpt-4.1-nano": "openai",
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "o3": "openai",
    "o3-mini": "openai",
    "o4-mini": "openai",
    "gemini-2.5-pro": "gemini",
    "gemini-2.5-flash": "gemini",
    "gemini-2.5-flash-lite": "gemini",
    "gemini-2.0-flash": "gemini",
    "gemini-2.0-flash-lite": "gemini",
}
PUBLIC_MODEL_NAMES: tuple[str, ...] = tuple(PUBLIC_MODEL_PROVIDERS)


def normalize_model_name(model_name: str) -> str:
    """Return the comparable form of a supplied model name."""

    return model_name.strip()


def resolve_llm_provider(model_name: str) -> str | None:
    """
    Return the provider serving this model.

    Returns ``None`` for any name outside the supported private and public
    models, which is the case Athena reports as unsupported.
    """

    normalized = normalize_model_name(model_name).lower()
    if not normalized:
        return None
    if normalized in PRIVATE_MODEL_NAMES:
        return PRIVATE_PROVIDER
    return PUBLIC_MODEL_PROVIDERS.get(normalized)


def is_private_model(model_name: str) -> bool:
    """Whether the model is served by the private vLLM deployment."""

    return normalize_model_name(model_name).lower() in PRIVATE_MODEL_NAMES


def is_supported_model(model_name: str) -> bool:
    """Whether Athena recognises this model name."""

    return resolve_llm_provider(model_name) is not None


def pricing_unavailable_message(model_name: str, provider: str | None) -> str:
    """Explain that no published rate covers this model's token usage."""

    normalized = normalize_model_name(model_name)
    if provider:
        return (
            f"Pricing not available for the model '{normalized}'. The gateway "
            f"routes it to {provider}, but no published rate is configured, so "
            "its token usage will not appear in cost reports."
        )
    return (
        f"Pricing not available for the model '{normalized}'. C2AI supports "
        "only its listed models, so this name's usage will not appear in cost "
        "reports."
    )
