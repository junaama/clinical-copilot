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

# Vision-capable model used by the VLM extraction pipeline (issue 004). Sonnet
# 4 reads typed and printed forms reliably; Haiku lacks the precision for
# small-font lab values, Opus is overkill. Anthropic-only — gpt-4o vision is
# not wired in (the rest of the agent runs against either provider, but the
# document pipeline always uses Anthropic).
_VISION_DEFAULT = "claude-sonnet-4-6"


def build_chat_model(settings: Settings, *, temperature: float = 0.0) -> BaseChatModel:
    provider = settings.llm_provider.lower().strip()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not settings.openai_api_key.get_secret_value():
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        return ChatOpenAI(
            model=settings.llm_model or _OPENAI_DEFAULT,
            temperature=temperature,
            api_key=settings.openai_api_key,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key.get_secret_value():
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return ChatAnthropic(
            model=settings.llm_model or _ANTHROPIC_DEFAULT,
            temperature=temperature,
            api_key=settings.anthropic_api_key,
        )

    raise RuntimeError(f"unknown LLM_PROVIDER {settings.llm_provider!r}")


def build_vision_model(
    settings: Settings,
    *,
    model_name: str | None = None,
    temperature: float = 0.0,
    timeout: float = 60.0,
    max_retries: int = 1,
) -> BaseChatModel:
    """Return a vision-capable Anthropic chat model for VLM extraction.

    The document-extraction pipeline (issue 004) always uses Anthropic — the
    primary chat-model provider may be OpenAI for cost reasons, but VLM goes
    through Sonnet 4 regardless. ``ANTHROPIC_API_KEY`` must be set or this
    raises at construction time so callers fail fast rather than at the first
    invocation.

    ``timeout`` defaults to 60s (multi-page PDF extractions are slow);
    ``max_retries`` defaults to 1 because the caller already wraps the call
    in its own retry/error envelope. ``model_name`` overrides
    ``settings.vlm_model`` for one-off calls (e.g. tests pinning to a
    specific snapshot).
    """

    from langchain_anthropic import ChatAnthropic

    if not settings.anthropic_api_key.get_secret_value():
        raise RuntimeError("build_vision_model requires ANTHROPIC_API_KEY")
    resolved_model = model_name or settings.vlm_model or _VISION_DEFAULT
    return ChatAnthropic(
        model=resolved_model,
        temperature=temperature,
        api_key=settings.anthropic_api_key,
        timeout=timeout,
        max_retries=max_retries,
    )
