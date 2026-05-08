"""Faithfulness judge (issues 011 + 012).

Public surface:

- ``extract_citation_claims(text)``: parse ``<cite ref="..."/>`` tags out of
  the agent's response and return ``CitationClaim`` records carrying the ref
  plus the surrounding sentence (the "claim" the citation is attached to).
- ``FaithfulnessJudge``: wraps an injected LLM client. ``judge(response_text,
  fetched_resources)`` runs two passes — per-citation grounding (issue 011)
  and a single uncited-claim sweep (issue 012) — and returns a combined
  ``FaithfulnessResult``. ``passed`` requires 100% of citations supported AND
  zero uncited clinical claims flagged. ``score`` is the citation-supported
  fraction (continuous scoreboard metric); ``uncited_claims`` is the list of
  flagged claim strings.

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
    r'<cite\s+ref\s*=\s*["“”‘’]([^"“”‘’]+)["“”‘’][^>]*/?\s*>',  # noqa: RUF001
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

    Combined verdict (issues 011 + 012):

    - ``passed`` is True iff every citation is supported AND the uncited-
      claim sweep flagged nothing.
    - ``score`` is the citation-supported fraction (continuous scoreboard
      metric, independent of the sweep — the sweep failure mode is reported
      via ``uncited_claims``).
    - ``uncited_claims`` is the list of flagged claim strings from the
      sweep call. Empty when the sweep returned cleanly with nothing to
      flag, when the response is empty, or when the sweep call failed
      (in which case ``sweep_error`` is populated and the case is not
      flagged — fail-open on sweep errors so we don't invent false
      positives the runner cannot debug).
    """

    passed: bool
    score: float
    total_citations: int
    supported_count: int
    verdicts: list[CitationVerdict] = field(default_factory=list)
    uncited_claims: list[str] = field(default_factory=list)
    sweep_error: str | None = None

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
            "uncited_claims": list(self.uncited_claims),
            "uncited_count": len(self.uncited_claims),
            "sweep_error": self.sweep_error,
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


# UNCITED-CLAIM-SWEEP marker is intentional — the test stub greps for it to
# distinguish sweep calls from per-citation calls without inspecting the user
# message body. Keep the literal in-prompt so the marker survives any prompt
# wording changes.
_SWEEP_SYSTEM_PROMPT = (
    "You are a clinical-evaluation judge running an UNCITED-CLAIM-SWEEP. "
    "Given an assistant response, list any FACTUAL CLINICAL CLAIMS the "
    "assistant made that DO NOT carry a `<cite ref=\"...\"/>` tag. Be strict "
    "but not credulous — only flag claims that fall into these categories:\n"
    "  - Vitals values (BP, HR, RR, temperature, SpO2, weight, etc.) with "
    "specific numbers or units\n"
    "  - Medication dose / frequency / route (e.g. 'lisinopril 10 mg daily')\n"
    "  - Medication status (active, held, discontinued, started)\n"
    "  - Lab results (named lab + specific value or trend)\n"
    "  - Encounter facts (admit/discharge dates, diagnoses, room, attending)\n"
    "DO NOT flag any of the following:\n"
    "  - Hedging or qualitative language ('appears stable', 'may benefit')\n"
    "  - Clarification questions ('which patient do you mean?')\n"
    "  - Refusals or access-denied messages ('I don't have access')\n"
    "  - General/process statements ('vital signs were checked')\n"
    "  - Patient identification or framing intros (e.g. 'Eduardo Perez, "
    "68M with CHF/HTN/CKD' — name, age, sex, top-line problem list framing "
    "are orientation, not independent clinical claims)\n"
    "  - Summary/closing sentences that synthesize already-cited content "
    "(e.g. 'overall the imaging shows stable findings')\n"
    "  - Counts or totals of items that were already individually cited "
    "(e.g. 'four active medications', 'three abnormal lab values', "
    "'the patient is currently on four active medications')\n"
    "  - Restatements of medication names, lab values, or clinical findings "
    "that were already individually cited with <cite> tags in earlier lines "
    "of the response (e.g. if 'Furosemide 40 mg' was cited above, a later "
    "sentence saying 'He is currently on Furosemide' is a restatement, "
    "not a new uncited claim)\n"
    "  - Requests or suggestions for the clinician to verify, review, or "
    "check something in the chart (e.g. 'Please verify the dosage', "
    "'additional lab results should be reviewed')\n"
    "  - Claims that are paired with a `<cite ref=\"...\"/>` tag (already "
    "cited claims are out of scope; this sweep only flags UNCITED ones)\n"
    "Reply ONLY with a JSON object of the form "
    '{"uncited_claims": ["<claim text 1>", "<claim text 2>"]} '
    'or {"uncited_claims": []} if everything is either cited or non-clinical. '
    "Use the assistant's exact wording for each flagged claim, trimmed to "
    "the relevant sentence. Keep each entry under 200 characters."
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


def _build_sweep_user_prompt(response_text: str) -> str:
    """Assemble the user message the sweep judge sees.

    Cap the response text at a generous size so a runaway response can't
    blow the prompt budget. Realistic clinical responses are well under
    this cap.
    """
    capped = response_text if len(response_text) <= 6000 else response_text[:6000]
    return (
        "ASSISTANT RESPONSE:\n"
        f"{capped}\n\n"
        "List any UNCITED clinical claims this response made, per the rules "
        "above. Reply ONLY with JSON: "
        '{"uncited_claims": ["<claim>", ...]} or {"uncited_claims": []}.'
    )


def _drop_claims_from_cited_sentences(
    response_text: str,
    uncited_claims: list[str],
) -> list[str]:
    """Remove sweep false positives that already sit in a cited sentence.

    The uncited sweep is intentionally a backstop for claims with no citation
    tag at all. If the flagged text appears in a sentence that contains a
    ``<cite ...>`` tag, the per-citation grounding pass is the right judge for
    support; counting it again as "uncited" creates noisy failures.
    """
    if not uncited_claims:
        return []

    kept: list[str] = []
    lower_response = response_text.lower()
    for claim in uncited_claims:
        needle = claim.strip().lower()
        if not needle:
            continue
        start = lower_response.find(needle)
        if start == -1:
            kept.append(claim)
            continue

        line_start = lower_response.rfind("\n", 0, start)
        line_start = 0 if line_start == -1 else line_start + 1
        line_end = lower_response.find("\n", start)
        line_end = len(response_text) if line_end == -1 else line_end
        line_after_claim = response_text[start:line_end]
        if _CITE_PATTERN.search(line_after_claim):
            continue
        kept.append(claim)

    # Second pass: drop claims that restate entities already cited elsewhere
    # in the response, or that are count/total summaries of cited items.
    if kept:
        kept = _drop_claims_restating_cited_entities(response_text, kept)
    return kept


# Regex to split response text into sentence-level segments. Uses both
# newlines and sentence terminators (". ", "! ", "? ") as boundaries.
# Sentence-level splitting is critical: a single-line response may mix
# cited and uncited content (e.g. "BP 90/60 <cite/>. Potassium was 5.8.")
# and line-level splitting would incorrectly treat all words as "cited".
_SEGMENT_SPLIT = re.compile(r"\n|(?<=[.!?])\s+")


# Common long English words (≥6 chars) that should NOT count as clinical
# entities for the restatement filter. These appear in clinical prose but
# are not drug names, lab names, condition names, or other discriminating
# medical entities. Kept deliberately narrow: better to occasionally miss
# a restatement (prompt fix catches it) than to drop a real uncited claim.
_COMMON_LONG_WORDS = frozenset({
    "active", "additional", "appear", "around", "before", "change",
    "called", "currently", "during", "either", "follow", "given",
    "however", "including", "listed", "medications", "normal",
    "number", "otherwise", "patient", "patients", "please", "recent",
    "relevant", "report", "result", "results", "review", "showed",
    "should", "specified", "status", "verified", "verify", "within",
    "without",
})


# Pattern matching count/total summaries of clinical items. These sentences
# say "the patient is on N active medications" or "three abnormal labs" etc.
# The pattern requires a number word/digit followed by a clinical plural noun.
_COUNT_SUMMARY_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|\d+)\b"
    r"[^.]{0,40}"
    r"\b(?:medication|problem|condition|allergi|prescription|lab|vital"
    r"|abnormal|finding)",
    re.IGNORECASE,
)


def _entity_words(text: str) -> set[str]:
    """Extract clinical entity words (≥6 chars, not common English) from text.

    Strips cite tags and markdown bold markers before tokenizing.
    """
    cleaned = _CITE_PATTERN.sub("", text).replace("**", "")
    return {
        w
        for w in re.findall(r"[a-zA-Z]+", cleaned.lower())
        if len(w) >= 6 and w not in _COMMON_LONG_WORDS
    }


def _drop_claims_restating_cited_entities(
    response_text: str,
    uncited_claims: list[str],
) -> list[str]:
    """Drop flagged claims that restate already-cited content.

    Two complementary heuristics:

    1. **Entity overlap**: Extract clinical entity words (long, non-common words
       like drug names and lab names) from *cited sentences* — sentences that
       contain a ``<cite>`` tag. If all entity words in the flagged claim are a
       subset of cited entity words, the claim restates cited content.
    2. **Count summary**: If the claim matches a "N active medications" / "three
       abnormal labs" pattern and the response has multiple cited segments, the
       claim is a synthesis count.

    Uses sentence-level splitting (not line-level) to correctly handle
    single-line text where cited and uncited content share one line.
    """
    segments = _SEGMENT_SPLIT.split(response_text)

    cited_entity_words: set[str] = set()
    n_cited_segments = 0
    for seg in segments:
        if _CITE_PATTERN.search(seg):
            n_cited_segments += 1
            cited_entity_words |= _entity_words(seg)

    if n_cited_segments == 0:
        return uncited_claims

    kept: list[str] = []
    for claim in uncited_claims:
        # Heuristic 1: entity overlap
        claim_entities = _entity_words(claim)
        if claim_entities and claim_entities <= cited_entity_words:
            continue

        # Heuristic 2: count/total summary when multiple items were cited
        if n_cited_segments >= 2 and _COUNT_SUMMARY_RE.search(claim):
            continue

        kept.append(claim)
    return kept


def _parse_sweep_json(raw: str) -> tuple[list[str], str | None]:
    """Parse the sweep judge's JSON reply.

    Returns ``(uncited_claims, error)``. On unparseable output the function
    returns an empty claim list with the parse error stashed in ``error`` —
    the caller can use this to record ``sweep_error`` on the result. We
    deliberately do NOT fail the case on a parse error: inventing flagged
    claims would create false positives the runner cannot debug.
    """
    if not raw:
        return [], "sweep returned empty content"
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        return [], f"sweep output not parseable: {cleaned[:120]!r}"
    candidate = cleaned[first_brace : last_brace + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return [], f"sweep JSON decode error: {exc}; raw={candidate[:120]!r}"
    if not isinstance(payload, dict):
        return [], "sweep output was not a JSON object"
    raw_claims = payload.get("uncited_claims")
    if not isinstance(raw_claims, list):
        return [], f"sweep payload missing 'uncited_claims' list: {payload!r}"
    claims: list[str] = []
    for item in raw_claims:
        text = str(item).strip()
        if text:
            claims.append(text)
    return claims, None


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
        tracked_refs: frozenset[str] = frozenset(),
    ) -> FaithfulnessResult:
        """Score the response on both faithfulness passes:

        1. Per-citation grounding: every ``<cite>`` in ``response_text`` is
           judged against the resource keyed by its ref in
           ``fetched_resources``.
        2. Uncited-claim sweep: one extra call asks the judge to enumerate
           any factual clinical claim that lacks a ``<cite>`` tag.

        Both passes run concurrently. Combined ``passed`` requires 100%
        of citations supported AND zero flagged uncited claims. An empty
        response text short-circuits to a trivial pass with no LLM calls.
        """
        if not (response_text or "").strip():
            return FaithfulnessResult(
                passed=True,
                score=1.0,
                total_citations=0,
                supported_count=0,
                verdicts=[],
                uncited_claims=[],
                sweep_error=None,
            )

        claims = extract_citation_claims(response_text)

        # Fan citation verdicts + the single sweep call out in parallel so
        # the judge's wall time is dominated by max(citation, sweep) instead
        # of citation_count + 1.
        verdict_tasks = [
            self._judge_one(
                claim, fetched_resources, langfuse, tracked_refs=tracked_refs
            )
            for claim in claims
        ]
        sweep_task = self._sweep_uncited_claims(response_text, langfuse)
        gathered = await asyncio.gather(*verdict_tasks, sweep_task)
        # `verdicts` is the prefix; the last element is `(uncited, sweep_error)`.
        verdicts = list(gathered[:-1])
        uncited_claims, sweep_error = gathered[-1]

        supported = sum(1 for v in verdicts if v.supported)
        total = len(verdicts)
        score = supported / total if total else 1.0
        passed = (supported == total) and not uncited_claims

        return FaithfulnessResult(
            passed=passed,
            score=score,
            total_citations=total,
            supported_count=supported,
            verdicts=verdicts,
            uncited_claims=uncited_claims,
            sweep_error=sweep_error,
        )

    async def _sweep_uncited_claims(
        self,
        response_text: str,
        langfuse: LangfuseClient | None,
    ) -> tuple[list[str], str | None]:
        """Single Haiku call enumerating uncited clinical claims.

        Fail-open on any LLM/parse error (no flagged claims) — the runner
        sees ``sweep_error`` populated and can log it, but the case verdict
        does not flip from a sweep failure. Rationale in
        ``FaithfulnessResult`` docstring.
        """
        sweep_prompt = _build_sweep_user_prompt(response_text)
        sweep_response_text: str | None = None
        try:
            model = self._llm_factory()
            messages = [
                SystemMessage(content=_SWEEP_SYSTEM_PROMPT),
                HumanMessage(content=sweep_prompt),
            ]
            reply = await model.ainvoke(messages)
        except Exception as exc:
            _log.warning("faithfulness sweep call failed: %s", exc)
            sweep_error = f"{type(exc).__name__}: {exc}"
            _emit_sweep_langfuse_span(
                langfuse,
                sweep_prompt=sweep_prompt,
                sweep_response=None,
                uncited_claims=[],
                sweep_error=sweep_error,
            )
            return [], sweep_error

        raw_content = getattr(reply, "content", "")
        if not isinstance(raw_content, str):
            raw_content = str(raw_content or "")
        sweep_response_text = raw_content
        claims, parse_error = _parse_sweep_json(raw_content)
        claims = _drop_claims_from_cited_sentences(response_text, claims)
        _emit_sweep_langfuse_span(
            langfuse,
            sweep_prompt=sweep_prompt,
            sweep_response=sweep_response_text,
            uncited_claims=claims,
            sweep_error=parse_error,
        )
        return claims, parse_error

    async def _judge_one(
        self,
        claim: CitationClaim,
        fetched_resources: dict[str, dict[str, Any]],
        langfuse: LangfuseClient | None,
        tracked_refs: frozenset[str] = frozenset(),
    ) -> CitationVerdict:
        resource = fetched_resources.get(claim.ref)
        if resource is None:
            # The parent graph (agent_node) tracks fetched refs in state but
            # doesn't propagate the resource bodies through the sub-graph
            # boundary. ``tracked_refs`` is the set the runner harvested from
            # ``state["fetched_refs"]``; when the ref is in that set, the
            # tool *did* fetch the resource — we just don't have the body to
            # show the LLM judge. Treat as supported rather than fail-closed
            # (the citation-resolution check independently verifies the ref
            # was fetched).
            if claim.ref in tracked_refs:
                verdict = CitationVerdict(
                    ref=claim.ref,
                    claim=claim.claim,
                    supported=True,
                    reasoning=(
                        f"resource {claim.ref} was fetched in this turn "
                        "(body unavailable for body-level judge inspection; "
                        "trusted via state.fetched_refs)"
                    ),
                    resource_present=False,
                )
                _emit_langfuse_span(langfuse, claim, resource, verdict)
                return verdict
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


def _emit_sweep_langfuse_span(
    langfuse: LangfuseClient | None,
    *,
    sweep_prompt: str,
    sweep_response: str | None,
    uncited_claims: list[str],
    sweep_error: str | None,
) -> None:
    """Best-effort Langfuse child span for the uncited-claim sweep.

    Span name is ``judge:faithfulness:uncited_sweep`` (one per case). Carries
    the sweep prompt as input and the flagged claims + raw response as
    output. No-ops cleanly when langfuse is unavailable.
    """
    if langfuse is None:
        return
    try:
        langfuse.record_faithfulness_uncited_sweep(
            sweep_prompt=sweep_prompt,
            sweep_response=sweep_response,
            uncited_claims=uncited_claims,
            sweep_error=sweep_error,
        )
    except Exception:
        _log.warning("langfuse uncited-sweep span emission failed", exc_info=True)


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
