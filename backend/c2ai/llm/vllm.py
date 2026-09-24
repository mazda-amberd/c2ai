
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from c2ai.config import get_settings
from c2ai.llm.config import get_model_config


def create_vllm_chat_model(chat_model_config=None):
    """Creates a vLLM chat model via OpenAI-compatible API."""
    model_config = get_model_config(chat_model_config)
    endpoint_env = model_config.get("endpoint_env", "VLLM_ENDPOINT")
    settings = get_settings()
    endpoint = getattr(settings, endpoint_env.lower(), "")
    if not endpoint:
        raise ValueError(
            f"{endpoint_env} must be set to the vLLM OpenAI-compatible API URL."
        )

    return ChatOpenAI(
        model=model_config["model_name"],
        openai_api_key=settings.vllm_api_key or "EMPTY",
        openai_api_base=endpoint,
        max_tokens=model_config["max_tokens"],
        temperature=model_config["temperature"],
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


def create_vllm_embedding_model(embedding_config=None):
    """Creates embedding model via vLLM OpenAI-compatible API."""
    model_config = get_model_config(embedding_config)
    settings = get_settings()
    endpoint = settings.vllm_embedding_endpoint
    if not endpoint:
        raise ValueError(
            "VLLM_EMBEDDING_ENDPOINT must be set to the vLLM embeddings API URL."
        )

    return OpenAIEmbeddings(
        model=model_config.get("embed_model"),
        openai_api_key=settings.vllm_api_key or "EMPTY",
        openai_api_base=endpoint,
    )
