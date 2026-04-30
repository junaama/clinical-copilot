"""Chat-model factory.

Selects between OpenAI and Anthropic based on ``LLM_PROVIDER``. Default is
OpenAI today; flip the env var (and ``LLM_MODEL``) to swap. The rest of the
graph depends only on the LangChain ``BaseChatModel`` interface, so no other
file needs to know which vendor is in use.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from .config import Settings

_ANTHROPIC_DEFAULT = "claude-sonnet-4-6"
_OPENAI_DEFAULT = "gpt-4o-mini"


def build_chat_model(settings: Settings, *, temperature: float = 0.0) -> BaseChatModel:
    provider = settings.llm_provider.lower().strip()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not settings.openai_api_key:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        return ChatOpenAI(
            model=settings.llm_model or _OPENAI_DEFAULT,
            temperature=temperature,
            api_key=settings.openai_api_key,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return ChatAnthropic(
            model=settings.llm_model or _ANTHROPIC_DEFAULT,
            temperature=temperature,
            api_key=settings.anthropic_api_key,
        )

    raise RuntimeError(f"unknown LLM_PROVIDER {settings.llm_provider!r}")
