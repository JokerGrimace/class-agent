from typing import Optional

from app.config import settings
from app.core.llm.adapter import LLMAdapter
from app.core.llm.openai import OpenAIAdapter
from app.core.llm.ollama import OllamaAdapter


def create_llm_adapter(provider: Optional[str] = None) -> LLMAdapter:
    provider = provider or settings.llm_provider

    if provider == "openai":
        return OpenAIAdapter()
    elif provider == "ollama":
        return OllamaAdapter()
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
