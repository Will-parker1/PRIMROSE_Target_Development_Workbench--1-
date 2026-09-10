"""Configuration helpers for an OpenAI-compatible local model endpoint."""

import os

from langchain_openai import ChatOpenAI

def _timeout() -> float:
    try:
        return float(os.getenv("KG_LLM_TIMEOUT", "120"))
    except ValueError:
        return 120.0


def local_LLM(
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
):
    return ChatOpenAI(
        model=model or os.getenv("KG_MODEL_ID", "google/gemma-4-e4b"),
        base_url=base_url or os.getenv("KG_LM_STUDIO_URL", "http://127.0.0.1:1234/v1"),
        api_key=api_key or os.getenv("KG_LM_STUDIO_API_KEY", "lm-studio"),
        temperature=0,
        timeout=timeout if timeout is not None else _timeout(),
        max_retries=1,
    )


def select_model():
    """Return the configured local model client.

    Kept as a named entry point for callers that previously imported this stub.
    """
    return local_LLM()
