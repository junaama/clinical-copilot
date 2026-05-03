"""Citation-anchored faithfulness judge (issue 011).

Public surface (single-pass, citation-anchored only — uncited-claim sweep
ships in issue 012):

- ``extract_citation_claims(text)``: parse ``<cite ref="..."/>`` tags out of
  the agent's response and return ``CitationClaim`` records carrying the ref
  plus the surrounding sentence (the "claim" the citation is attached to).
- ``FaithfulnessJudge``: wraps an injected LLM client. ``judge(response_text,
  fetched_resources)`` returns a ``FaithfulnessResult``: per-citation verdicts
  plus an aggregate ``passed`` (100% supported) and ``score`` (fraction
  supported). Cases with zero citations pass trivially. A citation pointing
  at a resource the agent never fetched fails without an LLM call.

Tests live in ``agent/tests/test_faithfulness_judge.py`` and use a stub LLM
so the module is exercised without spending Anthropic tokens. The runner
(``copilot.eval.runner``) calls the judge after agent inference and attaches
the result as a ``DimensionResult`` named ``faithfulness``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from .case import DimensionResult

if TYPE_CHECKING:
    from .langfuse_client import LangfuseClient

_log = logging.getLogger(__name__)

# Mirror the citation pattern from ``evaluators.extract_citations`` so the
# judge and the existing extractor agree on what counts as a citation.
# Smart quotes are intentional (LLMs occasionally emit them); same pattern
# lives in evaluators.py.
_CITE_PATTERN = re.compile(
    r'<cite\s+ref\s*=\s*["“”‘’]([^"“”‘’]+)["“”‘’]\s*/?\s*>',  # noqa: RUF001
    flags=re.IGNORECASE,
)

# How many characters of context to include before the citation when the
# citation lands in the middle of a long paragraph without sentence
# terminators. Keeps the prompt small and the judge focused.
_CLAIM_CONTEXT_CHARS = 240


@dataclass(frozen=True)
class CitationClaim:
    """One citation extracted from the response, paired with the claim
    sentence the citation is attached to."""

    ref: str
    claim: str


@dataclass
class CitationVerdict:
    """Judge verdict for one citation. ``error`` is set when the LLM call
    raised — fail-closed (treated as unsupported) but kept distinct so the
    runner can surface the failure mode."""

    ref: str
    claim: str
    supported: bool
    reasoning: str
    error: str | None = None
    resource_present: bool = True


@dataclass
class FaithfulnessResult:
    """Aggregate faithfulness result for one response.

    ``passed`` requires 100% of citations to be supported (citation-anchored
    pass only — uncited-claim sweep lands in issue 012). ``score`` is the
    fraction supported, useful for the scoreboard's continuous metric. A
    response with zero citations passes with score 1.0 and empty verdicts.
    """

    passed: bool
    score: float
    total_citations: int
    supported_count: int
    verdicts: list[CitationVerdict] = field(default_factory=list)

    def to_dimension_result(self) -> DimensionResult:
        details: dict[str, Any] = {
            "citations_supported": self.score,
            "supported_count": self.supported_count,
            "total_citations": self.total_citations,
            "verdicts": [
                {
                    "ref": v.ref,
                    "claim": v.claim,
                    "supported": v.supported,
                    "reasoning": v.reasoning,
                    "error": v.error,
                    "resource_present": v.resource_present,
                }
                for v in self.verdicts
            ],
            "unsupported": [
                {"ref": v.ref, "claim": v.claim, "reasoning": v.reasoning}
                for v in self.verdicts
                if not v.supported
            ],
        }
        return DimensionResult(
            name="faithfulness",
            passed=self.passed,
            score=self.score,
            details=details,
        )


def extract_citation_claims(text: str) -> list[CitationClaim]:
    """Pull citations + their attached claim sentences out of response text.

    Malformed citations (missing ref, empty ref, unquoted) are silently
    dropped. The returned list preserves order and may contain duplicate
    refs — the same resource cited twice for two different claims must be
    judged twice.
    """
    if not text:
        return []
    claims: list[CitationClaim] = []
    for match in _CITE_PATTERN.finditer(text):
        ref = (match.group(1) or "").strip()
        if not ref:
            continue
        claim_text = _claim_for_citation(text, match.start(), match.end())
        claims.append(CitationClaim(ref=ref, claim=claim_text))
    return claims


def _claim_for_citation(text: str, start: int, end: int) -> str:
    """Return the sentence (or trailing context) the citation belongs to.

    Strategy: look backward from the citation tag for the previous sentence
    terminator (``.``, ``!``, ``?``, newline) and forward for the next one.
    Capped at ``_CLAIM_CONTEXT_CHARS`` of leading context to keep prompts
    short. The citation tag itself is stripped from the returned claim so
    the judge sees just the human-readable claim.
    """
    # Walk backward to a sentence terminator.
    left = max(0, start - _CLAIM_CONTEXT_CHARS)
    window_back = text[left:start]
    sentence_start = left
    for terminator in (". ", "! ", "? ", "\n"):
        idx = window_back.rfind(terminator)
        if idx != -1:
            candidate = left + idx + len(terminator)
            if candidate > sentence_start:
                sentence_start = candidate

    # Walk forward to the next terminator.
    window_fwd = text[end : end + _CLAIM_CONTEXT_CHARS]
    sentence_end = len(text)
    for terminator in (". ", "! ", "? ", "\n"):
        idx = window_fwd.find(terminator)
        if idx != -1:
            candidate = end + idx + 1  # include the terminator char itself
            sentence_end = min(sentence_end, candidate)

    # If we never found a forward terminator, take the rest up to the cap.
    sentence_end = min(sentence_end, end + _CLAIM_CONTEXT_CHARS)

    raw = text[sentence_start:sentence_end]
    # Strip any other citation tags inside the claim so they don't pollute
    # the judge's view of "the claim".
    cleaned = _CITE_PATTERN.sub("", raw).strip()
    return cleaned


_SYSTEM_PROMPT = (
    "You are a clinical-evaluation judge. Decide whether a single CITED "
    "FHIR resource actually supports a specific CLAIM the assistant made. "
    "Answer ONLY with a JSON object of the form "
    '{"supported": true|false, "reasoning": "<short explanation>"}. '
    "A claim is supported if the cited resource's content directly justifies "
    "the specific factual statement in the claim. Numeric mismatches, "
    "wrong units, wrong status, wrong patient, or wrong time window all "
    "make the claim unsupported. Do not be charitable: if the resource "
    "does not contain the specific value/fact in the claim, say "
    '"supported": false. Keep reasoning under 200 characters.'
)


def _build_user_prompt(ref: str, claim: str, resource: dict[str, Any]) -> str:
    """Assemble the per-citation user message the judge sees."""
    try:
        resource_json = json.dumps(resource, default=str, indent=2)[:2000]
    except (TypeError, ValueError):
        resource_json = repr(resource)[:2000]
    return (
        f"CITED REF: {ref}\n\n"
        f"CITED RESOURCE:\n{resource_json}\n\n"
        f"CLAIM: {claim}\n\n"
        "Does the cited resource support the claim? "
        'Reply ONLY with JSON: {"supported": true|false, "reasoning": "..."}.'
    )


def _parse_verdict_json(raw: str) -> tuple[bool, str]:
    """Parse the judge's JSON reply. Returns (supported, reasoning).

    Tolerates the model wrapping the JSON in prose by extracting the first
    ``{...}`` block. On unparseable output, fails closed with a marker
    reasoning so the runner can surface the situation rather than passing
    silently.
    """
    if not raw:
        return False, "judge returned empty content"
    cleaned = raw.strip()
    # Some models wrap JSON in ```json ... ```; strip the fence.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    # Locate the first JSON object boundary.
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        return False, f"judge output not parseable: {cleaned[:120]!r}"
    candidate = cleaned[first_brace : last_brace + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return False, f"judge JSON decode error: {exc}; raw={candidate[:120]!r}"
    if not isinstance(payload, dict):
        return False, "judge output was not a JSON object"
    supported = bool(payload.get("supported"))
    reasoning = str(payload.get("reasoning") or "(no reasoning provided)")
    return supported, reasoning


class FaithfulnessJudge:
    """Citation-anchored faithfulness judge (Haiku 4.5 by default).

    Constructed with an injected ``llm_factory`` so tests can swap a stub
    in without touching the real Anthropic SDK. The factory returns a chat
    model that exposes ``ainvoke(messages)`` and an ``AIMessage``-shaped
    reply with a ``content`` string — same surface the title summarizer
    uses, so the same stub pattern applies.

    Per-citation calls run concurrently via ``asyncio.gather``. The whole
    judge call is best-effort: an LLM exception on one citation marks that
    one citation as unsupported (with the error stashed) but does not abort
    judging the others.
    """

    def __init__(
        self,
        *,
        llm_factory: Callable[[], Any],
        model_name: str = "claude-haiku-4-5",
    ) -> None:
        self._llm_factory = llm_factory
        self._model_name = model_name

    async def judge(
        self,
        response_text: str,
        fetched_resources: dict[str, dict[str, Any]],
        *,
        langfuse: LangfuseClient | None = None,
    ) -> FaithfulnessResult:
        """Score every ``<cite>`` in ``response_text`` against the resource
        it points at in ``fetched_resources``. Returns a structured result.
        """
        claims = extract_citation_claims(response_text)
        if not claims:
            return FaithfulnessResult(
                passed=True,
                score=1.0,
                total_citations=0,
                supported_count=0,
                verdicts=[],
            )

        verdicts = await asyncio.gather(
            *(self._judge_one(claim, fetched_resources, langfuse) for claim in claims),
            return_exceptions=False,
        )

        supported = sum(1 for v in verdicts if v.supported)
        total = len(verdicts)
        score = supported / total if total else 1.0
        return FaithfulnessResult(
            passed=supported == total,
            score=score,
            total_citations=total,
            supported_count=supported,
            verdicts=list(verdicts),
        )

    async def _judge_one(
        self,
        claim: CitationClaim,
        fetched_resources: dict[str, dict[str, Any]],
        langfuse: LangfuseClient | None,
    ) -> CitationVerdict:
        resource = fetched_resources.get(claim.ref)
        if resource is None:
            verdict = CitationVerdict(
                ref=claim.ref,
                claim=claim.claim,
                supported=False,
                reasoning=(
                    f"resource {claim.ref} was not fetched in this turn; "
                    "cannot verify claim against an absent resource"
                ),
                resource_present=False,
            )
            _emit_langfuse_span(langfuse, claim, resource, verdict)
            return verdict

        try:
            model = self._llm_factory()
            messages = [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=_build_user_prompt(claim.ref, claim.claim, resource)),
            ]
            reply = await model.ainvoke(messages)
        except Exception as exc:
            _log.warning(
                "faithfulness judge call failed ref=%s err=%s", claim.ref, exc
            )
            verdict = CitationVerdict(
                ref=claim.ref,
                claim=claim.claim,
                supported=False,
                reasoning=f"judge call raised: {type(exc).__name__}: {exc}",
                error=f"{type(exc).__name__}: {exc}",
            )
            _emit_langfuse_span(langfuse, claim, resource, verdict)
            return verdict

        raw_content = getattr(reply, "content", "")
        if not isinstance(raw_content, str):
            raw_content = str(raw_content or "")

        supported, reasoning = _parse_verdict_json(raw_content)
        verdict = CitationVerdict(
            ref=claim.ref,
            claim=claim.claim,
            supported=supported,
            reasoning=reasoning,
        )
        _emit_langfuse_span(langfuse, claim, resource, verdict)
        return verdict


def _emit_langfuse_span(
    langfuse: LangfuseClient | None,
    claim: CitationClaim,
    resource: dict[str, Any] | None,
    verdict: CitationVerdict,
) -> None:
    """Best-effort Langfuse child span for one citation verdict.

    No-ops cleanly when langfuse is None or disabled. Wrapped in a
    try/except so observability bugs never break the eval run.
    """
    if langfuse is None:
        return
    try:
        langfuse.record_faithfulness_citation(claim=claim, resource=resource, verdict=verdict)
    except Exception:
        _log.warning("langfuse faithfulness span emission failed", exc_info=True)


def build_default_haiku_factory(
    api_key: str, model_name: str = "claude-haiku-4-5"
) -> Callable[[], Any]:
    """Return a factory that constructs a ChatAnthropic Haiku judge instance.

    Lifted out of ``FaithfulnessJudge`` so tests don't need to install or
    import langchain_anthropic. The factory runs every call (cheap object
    construction; each call gets a fresh client to keep concurrent
    judgments independent).
    """

    def _factory() -> Any:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name,
            temperature=0.0,
            api_key=api_key,
            timeout=20.0,
            max_retries=1,
        )

    return _factory
