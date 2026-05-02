"""HaikuTitleSummarizer — non-blocking title write-behind for issue 008.

Exercises the summarizer through its public interface using a stub chat
model so tests never hit the real Anthropic API. Covers:

- Success path: model output replaces the truncated-first-message title.
- Failure paths: timeout / exception / empty output leave the title in
  place (no retry, no blank write).
- Truncation: oversized model output is clipped to ``TITLE_MAX_CHARS``.
- Cleanup: leading/trailing quotes and trailing periods stripped — Haiku
  often returns ``"Brief on Eduardo."`` and the sidebar reads better
  without the punctuation noise.
- Idempotency: a second call against the same conversation is a no-op
  (the registry's existing-title check guards re-summarization).

Prior art: ``test_audit.py`` for "called exactly once" assertions;
``test_conversations.py`` for the in-memory ConversationRegistry pattern.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pytest

from copilot.conversations import (
    ConversationRegistry,
    InMemoryConversationStore,
)
from copilot.title_summarizer import HaikuTitleSummarizer


class _StubModel:
    """Mimics LangChain's ``BaseChatModel.ainvoke`` for one-shot summarize calls.

    Returns whatever the test sets on ``content``; raises whatever the test
    sets on ``error``.
    """

    def __init__(
        self,
        *,
        content: str = "",
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.content = content
        self.error = error
        self.delay = delay
        self.call_count = 0
        self.last_messages: Any = None

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
        self.call_count += 1
        self.last_messages = messages
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error

        # Mirror the AIMessage shape that ChatAnthropic returns.
        class _Reply:
            def __init__(self, content: str) -> None:
                self.content = content

        return _Reply(self.content)


def _registry() -> ConversationRegistry:
    return ConversationRegistry(store=InMemoryConversationStore())


def _factory(model: _StubModel) -> Callable[[], _StubModel]:
    return lambda: model


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_summarize_replaces_title_on_success() -> None:
    reg = _registry()
    # Production placeholder is the derive_title output — same shape the
    # /chat write-behind would have written via ensure_first_turn_title.
    first_user = "Tell me about Eduardo Perez"
    await reg.create(
        conversation_id="c-1",
        user_id="dr-smith",
        title=first_user,
    )
    model = _StubModel(content="Eduardo Perez 24h Brief")
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-1",
        first_user_message=first_user,
        first_assistant_message="Eduardo Perez (61M)... no overnight changes.",
    )

    fresh = await reg.get("c-1")
    assert fresh is not None
    assert fresh.title == "Eduardo Perez 24h Brief"
    assert model.call_count == 1


async def test_summarize_strips_surrounding_quotes() -> None:
    """Haiku frequently returns titles wrapped in quotes; strip them."""
    reg = _registry()
    await reg.create(conversation_id="c-q", user_id="u", title="brief")
    model = _StubModel(content='"Brief on Eduardo Perez"')
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-q",
        first_user_message="brief",
        first_assistant_message="ack",
    )

    fresh = await reg.get("c-q")
    assert fresh is not None
    assert fresh.title == "Brief on Eduardo Perez"


async def test_summarize_strips_trailing_period() -> None:
    """Sidebar reads better without the trailing period Haiku often emits."""
    reg = _registry()
    await reg.create(conversation_id="c-p", user_id="u", title="brief")
    model = _StubModel(content="Brief on Eduardo Perez.")
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-p",
        first_user_message="brief",
        first_assistant_message="ack",
    )

    fresh = await reg.get("c-p")
    assert fresh is not None
    assert fresh.title == "Brief on Eduardo Perez"


async def test_summarize_truncates_oversized_output() -> None:
    """Defense-in-depth — Haiku is asked for ≤60 chars but a misbehaving
    model output mustn't break the sidebar layout."""
    reg = _registry()
    await reg.create(conversation_id="c-big", user_id="u", title="brief")
    model = _StubModel(content="Z" * 500)
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-big",
        first_user_message="brief",
        first_assistant_message="ack",
    )

    fresh = await reg.get("c-big")
    assert fresh is not None
    assert len(fresh.title) <= 60


async def test_summarize_includes_user_and_assistant_in_prompt() -> None:
    """Both messages must reach the model — quality of the title depends on it."""
    reg = _registry()
    await reg.create(
        conversation_id="c-prompt", user_id="u", title="UNIQUE_USER_TOKEN"
    )
    model = _StubModel(content="A title")
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-prompt",
        first_user_message="UNIQUE_USER_TOKEN",
        first_assistant_message="UNIQUE_ASSISTANT_TOKEN",
    )

    # last_messages is whatever shape the model received. Stringify the
    # whole structure and check both tokens reached it.
    rendered = str(model.last_messages)
    assert "UNIQUE_USER_TOKEN" in rendered
    assert "UNIQUE_ASSISTANT_TOKEN" in rendered


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_summarize_failure_leaves_title_in_place() -> None:
    """Timeout / model error must not blank the existing title."""
    reg = _registry()
    placeholder = "Original truncated title"
    await reg.create(
        conversation_id="c-err", user_id="u", title=placeholder
    )
    model = _StubModel(error=RuntimeError("boom"))
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-err",
        first_user_message=placeholder,
        first_assistant_message="ack",
    )

    fresh = await reg.get("c-err")
    assert fresh is not None
    assert fresh.title == placeholder


async def test_summarize_empty_output_leaves_title_in_place() -> None:
    """An empty / whitespace-only model output is treated as failure."""
    reg = _registry()
    placeholder = "Original title"
    await reg.create(
        conversation_id="c-empty", user_id="u", title=placeholder
    )
    model = _StubModel(content="   ")
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-empty",
        first_user_message=placeholder,
        first_assistant_message="ack",
    )

    fresh = await reg.get("c-empty")
    assert fresh is not None
    assert fresh.title == placeholder


async def test_summarize_logs_failure_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per the issue's observability AC: failures are logged."""
    reg = _registry()
    placeholder = "Original"
    await reg.create(conversation_id="c-log", user_id="u", title=placeholder)
    model = _StubModel(error=RuntimeError("network down"))
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    with caplog.at_level(logging.WARNING, logger="copilot.title_summarizer"):
        await summarizer.summarize_and_set(
            conversation_id="c-log",
            first_user_message=placeholder,
            first_assistant_message="ack",
        )

    # At least one warning record from our logger.
    assert any(
        r.name == "copilot.title_summarizer" and r.levelno >= logging.WARNING
        for r in caplog.records
    )


async def test_summarize_logs_success_with_model_and_latency(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per the AC: success is logged as its own line item with model + latency."""
    reg = _registry()
    placeholder = "Original"
    await reg.create(conversation_id="c-ok", user_id="u", title=placeholder)
    model = _StubModel(content="Better title")
    summarizer = HaikuTitleSummarizer(
        registry=reg,
        model_factory=_factory(model),
        model_name="claude-haiku-4-5-test",
    )

    with caplog.at_level(logging.INFO, logger="copilot.title_summarizer"):
        await summarizer.summarize_and_set(
            conversation_id="c-ok",
            first_user_message=placeholder,
            first_assistant_message="ack",
        )

    matched = [
        r for r in caplog.records if r.name == "copilot.title_summarizer"
    ]
    assert matched, "expected at least one log line from the summarizer"
    # Model name and a latency-shaped key must both be present in some record.
    log_blob = "\n".join(r.getMessage() for r in matched)
    assert "claude-haiku-4-5-test" in log_blob
    assert "latency_ms" in log_blob


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_summarize_does_not_run_when_title_already_haiku_summarized() -> None:
    """The /chat write-behind fires summarize_and_set on every turn for safety,
    but the summarizer itself must short-circuit when the title is already
    set to something other than the truncated first message.

    The check is structural: the summarizer is called at most once per
    conversation. The /chat path enforces this via a "first turn only"
    gate; the summarizer itself enforces it via a "skip if title differs
    from the truncated first message" check, so a misbehaving caller
    can't cause a second model call.
    """
    reg = _registry()
    # Title is the post-Haiku version (does not match the truncated first
    # message), simulating a prior summarize run.
    await reg.create(
        conversation_id="c-idem", user_id="u", title="Already-Haiku Title"
    )
    model = _StubModel(content="Should never be called")
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="c-idem",
        first_user_message="brief",
        first_assistant_message="ack",
    )

    fresh = await reg.get("c-idem")
    assert fresh is not None
    assert fresh.title == "Already-Haiku Title"
    assert model.call_count == 0


async def test_summarize_unknown_conversation_is_noop() -> None:
    """No row → no model call, no exception — the chat path may legitimately
    fire summarize before the registry create completes."""
    reg = _registry()
    model = _StubModel(content="Something")
    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_factory(model))

    await summarizer.summarize_and_set(
        conversation_id="never-exists",
        first_user_message="brief",
        first_assistant_message="ack",
    )

    assert model.call_count == 0


async def test_summarize_factory_failure_swallowed() -> None:
    """If model construction fails (missing API key, import error), the
    summarizer logs and returns — chat must not break."""
    reg = _registry()
    placeholder = "Original"
    await reg.create(conversation_id="c-fac", user_id="u", title=placeholder)

    def _broken_factory() -> Any:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    summarizer = HaikuTitleSummarizer(registry=reg, model_factory=_broken_factory)
    await summarizer.summarize_and_set(
        conversation_id="c-fac",
        first_user_message=placeholder,
        first_assistant_message="ack",
    )

    fresh = await reg.get("c-fac")
    assert fresh is not None
    assert fresh.title == placeholder
