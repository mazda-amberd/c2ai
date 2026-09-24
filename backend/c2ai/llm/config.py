from enum import Enum


class Model(Enum):
    MODEL_CONFIG_NAME = "vllm_qwen3_coder_next"


MODEL_CONFIGS = {
    "vllm_qwen3_coder_next": {
        "deployment_name": "qwen3-coder-next",
        "deployment": "ada",
        "embed_model": "qwen-embedding",
        "tokenizer_model_name": "cl100k_base",
        "chunk_size": 1000,
        "temperature": 0.2,
        "top_k": 80,
        "gpu_layers": 300,
        "repeat_penalty": 1.8,
        "streaming": False,
        "model_name": "qwen-tier1",
        "pred_tokens": 2048,
        "max_tokens": 16294,
        "provider": "vllm",
        "endpoint_env": "VLLM_ENDPOINT",
    },
    "vllm_qwen3_6": {
        "deployment_name": "qwen3.6:35b-a3b-bf16",
        "deployment": "ada",
        "embed_model": "qwen-embedding",
        "tokenizer_model_name": "cl100k_base",
        "chunk_size": 1000,
        "temperature": 0.2,
        "top_k": 80,
        "gpu_layers": 300,
        "repeat_penalty": 1.8,
        "streaming": False,
        "model_name": "qwen-main",
        "pred_tokens": 2048,
        "max_tokens": 16294,
        "provider": "vllm",
        "endpoint_env": "VLLM_QWEN3_6_ENDPOINT",
    },
}

MODEL_CONFIG_ALIASES = {
    "qwen3_coder_next": "vllm_qwen3_coder_next",
    "qwen3_6": "vllm_qwen3_6"
}


def _normalize_config_key(config_type):
    if isinstance(config_type, dict):
        return config_type
    if isinstance(config_type, Enum):
        return config_type.value
    if not config_type:
        return Model.MODEL_CONFIG_NAME.value
    return str(config_type)


def get_model_config(config_type):
    """ Gets the model config for the given config type - infers the config type from the config type string

    Args:
        config_type (str): The config type string

    Returns:
        The model config as a dictionary
            chat_model_name (str): The name of the chat model
            embed_model_name (str): The name of the embed model
            tokenizer_model_name (str): The name of the tokenizer model
            model_temperature (float): The temperature of the model
            use_streaming (bool): Whether to use streaming or not
            use_azure (bool): Whether to use azure or not
            openai_api_version (str): OpenAi api version date
            model_name (str): OpenAi model name
            max_tokens (int): Maximum token number of model
    """

    normalized = _normalize_config_key(config_type)
    if isinstance(normalized, dict):
        return normalized.copy()

    key = MODEL_CONFIG_ALIASES.get(normalized, normalized)
    if key in MODEL_CONFIGS:
        return MODEL_CONFIGS[key].copy()

    # Compatibility fallback for partial names.
    if "qwen3_6" in normalized:
        return MODEL_CONFIGS["vllm_qwen3_6"].copy()

    return MODEL_CONFIGS[Model.MODEL_CONFIG_NAME.value].copy()

EMBEDDING_MODEL_NAME = "vllm_qwen3_coder_next"
