from c2ai.llm import vllm


def test_qwen3_6_uses_its_configured_endpoint(monkeypatch):
    captured = {}
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setenv("VLLM_QWEN3_6_ENDPOINT", "https://qwen.example/v1")
    monkeypatch.setattr(vllm, "ChatOpenAI", lambda **kwargs: captured.update(kwargs))

    vllm.create_vllm_chat_model("qwen3_6")

    assert captured["model"] == "qwen-main"
    assert captured["openai_api_base"] == "https://qwen.example/v1"
    assert captured["openai_api_key"] == "test-key"


def test_embedding_model_uses_embedding_endpoint(monkeypatch):
    captured = {}
    monkeypatch.setenv("VLLM_EMBEDDING_ENDPOINT", "https://embed.example/v1")
    monkeypatch.setattr(
        vllm, "OpenAIEmbeddings", lambda **kwargs: captured.update(kwargs)
    )

    vllm.create_vllm_embedding_model("qwen3_coder_next")

    assert captured["model"] == "qwen-embedding"
    assert captured["openai_api_base"] == "https://embed.example/v1"
