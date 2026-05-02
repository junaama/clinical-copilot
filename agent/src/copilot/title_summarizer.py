"""One-shot Haiku title summarizer (issue 008).

After the first turn of a conversation completes, this module takes the
first user message and the first assistant response and asks a Haiku-class
model to emit a short clinical-sidebar title (<=60 chars). The result
overwrites ``copilot_conversation.title`` so the sidebar stops showing the
truncated first message that issue 004 set as the placeholder.

Failure semantics - by design:

- Timeouts, model errors, empty output -> existing title is left in place.
  No retry. Better to ship a thread with a "Tell me about Edu..." title
  than to blank the row or stall the chat response.
- Unknown conversation_id -> silent no-op. The chat path may legitimately
  fire summarize before the registry create completes.
- Already-summarized title (registry's title differs from what would be
  derived from the first user message) -> no model call. This makes the
  summarizer safe to invoke more than once per conversation; the /chat
  write-behind also gates on first-turn-only as defense in depth.

Observability - per the AC:

- Every invocation logs one INFO line on success including model name and
  ``latency_ms``, one WARNING line on failure. The summarizer does NOT
  produce an ``agent_turn_audit`` row -- it is not a clinical turn.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .conversations import (
    TITLE_MAX_CHARS,
    ConversationRegistry,
    derive_title_from_message,
)

_log = logging.getLogger(__name__)

# Smart-quote codepoints LLMs frequently emit. Spelled via \u escapes so
# ruff RUF001 doesn't flag the file for "ambiguous unicode" -- these
# characters are load-bearing here, not accidental copy-paste artefacts.
_LDQ = "\u201c"  # LEFT DOUBLE QUOTATION MARK
_RDQ = "\u201d"  # RIGHT DOUBLE QUOTATION MARK
_LSQ = "\u2018"  # LEFT SINGLE QUOTATION MARK
_RSQ = "\u2019"  # RIGHT SINGLE QUOTATION MARK

# The instruction is short on purpose: every extra sentence in the system
# prompt eats Haiku's already-tight latency budget. The two rules that
# matter are length and quote-discipline; everything else (tone, domain
# framing) is pre-shaped by the user/assistant excerpts being clinical.
_SYSTEM_PROMPT = (
    "You generate short titles for a clinical Co-Pilot conversation sidebar. "
    "Output ONLY the title -- no quotes, no prefix, no period. "
    f"At most {TITLE_MAX_CHARS} characters. Capture the patient and the "
    "clinical intent (e.g., 'Eduardo Perez 24h Brief', 'Cooper antibiotic "
    "review'). If the conversation is generic, use a short topic phrase."
)


def _build_user_prompt(first_user: str, first_assistant: str) -> str:
    """Render the two-turn excerpt into a single Haiku prompt.

    Both halves are clipped: the assistant reply can be many paragraphs of
    clinical synthesis and we only need enough context to title it.
    """
    user_excerpt = (first_user or "").strip()[:500]
    assistant_excerpt = (first_assistant or "").strip()[:1000]
    return (
        f"User asked:\n{user_excerpt}\n\n"
        f"Assistant replied:\n{assistant_excerpt}\n\n"
        "Title:"
    )


def _clean_title(raw: str) -> str:
    """Strip the noise Haiku-class models commonly prepend or append.

    - Wrapping single/double quotes ("Brief on Eduardo" -> Brief on Eduardo)
    - Trailing period (Brief on Eduardo. -> Brief on Eduardo)
    - Leading "Title:" or similar polite prefix the model sometimes emits
    - Truncated to TITLE_MAX_CHARS as a backstop against a misbehaving model
    """
    cleaned = raw.strip()
    # Drop a trailing period without touching ellipses ('...').
    if cleaned.endswith(".") and not cleaned.endswith("..."):
        cleaned = cleaned[:-1].rstrip()
    # Drop matching wrapping quotes (single, double, smart-quote pairs).
    quote_pairs = (
        ('"', '"'),
        ("'", "'"),
        (_LDQ, _RDQ),
        (_LSQ, _RSQ),
    )
    for opener, closer in quote_pairs:
        if len(cleaned) >= 2 and cleaned[0] == opener and cleaned[-1] == closer:
            cleaned = cleaned[1:-1].strip()
            break
    # Common conversational lead-ins.
    for prefix in ("Title:", "title:", "TITLE:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    if len(cleaned) > TITLE_MAX_CHARS:
        cleaned = cleaned[:TITLE_MAX_CHARS].rstrip()
    return cleaned


class HaikuTitleSummarizer:
    """Owns the Haiku call + the registry write.

    Constructed with an injected ``model_factory`` so tests can swap a stub
    in without touching the real Anthropic SDK. Production code passes the
    default factory (``build_default_haiku_factory``) which returns a
    ChatAnthropic instance bound to ``ANTHROPIC_API_KEY``.

    The model name is logged on each call; defaults to the Anthropic SDK
    name for Haiku 4.5 but accepts an override so the dev can flip a
    Settings-driven name without rewiring the factory.
    """

    def __init__(
        self,
        *,
        registry: ConversationRegistry,
        model_factory: Callable[[], Any],
        model_name: str = "claude-haiku-4-5",
    ) -> None:
        self._registry = registry
        self._model_factory = model_factory
        self._model_name = model_name

    async def summarize_and_set(
        self,
        *,
        conversation_id: str,
        first_user_message: str,
        first_assistant_message: str,
    ) -> None:
        """Invoke Haiku, clean the result, write it to the registry.

        Always returns ``None`` and never raises -- the chat path treats
        this as fire-and-forget. Any failure is logged and the prior
        truncated-message title stays in place.
        """
        existing = await self._registry.get(conversation_id)
        if existing is None:
            # Race / unknown id -- same no-op semantics as the registry's
            # other write methods.
            return

        # Idempotency guard. The /chat write-behind is the canonical
        # first-turn-only gate; this is defense in depth so a misbehaving
        # caller can't trigger a second model call.
        truncated = derive_title_from_message(first_user_message)
        if existing.title and existing.title != truncated:
            return

        try:
            model = self._model_factory()
        except Exception as exc:
            _log.warning(
                "title summarizer model factory failed conv=%s model=%s error=%s",
                conversation_id,
                self._model_name,
                exc,
            )
            return

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(
                content=_build_user_prompt(
                    first_user_message, first_assistant_message
                )
            ),
        ]

        started = time.perf_counter()
        try:
            reply = await model.ainvoke(messages)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            _log.warning(
                "title summarizer call failed model=%s latency_ms=%d error=%s",
                self._model_name,
                latency_ms,
                exc,
            )
            return

        latency_ms = int((time.perf_counter() - started) * 1000)

        raw_content = getattr(reply, "content", "")
        if not isinstance(raw_content, str):
            raw_content = str(raw_content or "")
        title = _clean_title(raw_content)

        if not title:
            _log.warning(
                "title summarizer returned empty content model=%s latency_ms=%d",
                self._model_name,
                latency_ms,
            )
            return

        await self._registry.set_title(conversation_id, title)
        _log.info(
            "title summarizer ok model=%s latency_ms=%d title_len=%d",
            self._model_name,
            latency_ms,
            len(title),
        )


def build_default_haiku_factory(
    api_key: str, model_name: str = "claude-haiku-4-5"
) -> Callable[[], Any]:
    """Return a factory that constructs a ChatAnthropic Haiku instance.

    Invoked from server lifespan when ``ANTHROPIC_API_KEY`` is configured.
    Lifted out of ``HaikuTitleSummarizer`` so tests don't need to install
    or import langchain_anthropic.
    """

    def _factory() -> Any:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name,
            temperature=0.0,
            api_key=api_key,
            timeout=10.0,
            max_retries=0,
        )

    return _factory
