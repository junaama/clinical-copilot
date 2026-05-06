"""Synthesis-text → structured-block conversion.

The graph nodes (``triage_node``, ``agent_node``, ``clarify_node``,
``verifier_node``) call into this module to convert their free-text response
into the wire-shaped block defined in
``agentforge-docs/CHAT-API-CONTRACT.md``.

Strategy (Option A from the task brief): the LLM is asked for a structured
JSON object via ``model.with_structured_output``. On validation failure we
log and fall back to a ``PlainBlock`` carrying the raw text so we always
return a valid wire shape.

Citations are extracted from ``<cite ref="..."/>`` tags emitted in the
synthesis text and ratified against ``fetched_refs``. Unratified refs are
dropped here; the verifier still runs the full §13 check on the underlying
text and the regen loop applies if any claim is unsourced.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from .api.schemas import (
    Citation,
    CitationCard,
    CohortPatient,
    Delta,
    OvernightBlock,
    PlainBlock,
    TimelineEvent,
    TriageBlock,
    fhir_ref_to_card,
)

_log = logging.getLogger(__name__)

_CITE_PATTERN = re.compile(
    r'<cite\s+ref\s*=\s*["“”‘’]([^"“”‘’]+)["“”‘’][^>]*/?\s*>',
    flags=re.IGNORECASE,
)

# Match the full ``<cite ...>`` tag so we can pull additional attributes
# (``source``, ``section``, ``page``, ``field``, ``value``) out of the
# inner attribute string. Issue 027 surfaces ``source`` and ``section``
# in guideline citation labels.
_CITE_FULL_TAG_PATTERN = re.compile(
    r'<cite\s+([^>]*?)\s*/?\s*>',
    flags=re.IGNORECASE,
)
_CITE_INNER_REF_PATTERN = re.compile(
    r'ref\s*=\s*["“”‘’]([^"“”‘’]+)["“”‘’]',
    flags=re.IGNORECASE,
)
_CITE_ATTR_KEYS: tuple[str, ...] = ("source", "section", "page", "field", "value")


# Canonical follow-up chip strings, verbatim from the prototype copy. The
# placeholders are filled in when patient names are available; otherwise we
# drop the entry rather than ship a half-formatted string to the UI.
_TRIAGE_FOLLOWUPS: tuple[str, ...] = (
    "Draft an SBAR for {patient_name}",
    "Sort cohort by NEWS2 instead",
    "Open {next_patient_name}",
)
_OVERNIGHT_FOLLOWUPS: tuple[str, ...] = (
    "Suggest next orders",
    "Show last night's vitals trend",
    "Draft progress note",
)


def strip_cite_tags(text: str) -> str:
    """Remove ``<cite ref="..."/>`` tags so block ``lead`` is clean prose."""

    return _CITE_PATTERN.sub("", text or "").replace("  ", " ").strip()


def extract_cite_refs(text: str) -> list[str]:
    """Return the ordered, deduplicated set of refs cited in ``text``."""

    seen: list[str] = []
    for match in _CITE_PATTERN.finditer(text or ""):
        ref = match.group(1).strip()
        if ref and ref not in seen:
            seen.append(ref)
    return seen


def extract_cite_attributes(text: str) -> dict[str, dict[str, str]]:
    """Map cited ref → its trailing ``<cite/>`` attribute dict.

    The wire contract allows ``<cite ref="..." source="..." section="..."/>``
    style tags for guideline references and ``page="..." field="..."
    value="..."`` for document references. The verifier-level ``ref``
    extraction discards these attributes; this helper preserves them so
    downstream label construction can surface ``source``/``section`` on
    guideline chips.

    First occurrence wins on duplicate refs, mirroring
    :func:`extract_cite_refs`'s dedup-preserving-order semantics.
    """

    attrs_by_ref: dict[str, dict[str, str]] = {}
    for tag_match in _CITE_FULL_TAG_PATTERN.finditer(text or ""):
        inner = tag_match.group(1) or ""
        ref_match = _CITE_INNER_REF_PATTERN.search(inner)
        if not ref_match:
            continue
        ref = ref_match.group(1).strip()
        if not ref or ref in attrs_by_ref:
            continue
        attrs: dict[str, str] = {}
        for key in _CITE_ATTR_KEYS:
            attr_pattern = re.compile(
                rf'\b{key}\s*=\s*["“”‘’]([^"“”‘’]+)["“”‘’]',
                flags=re.IGNORECASE,
            )
            m = attr_pattern.search(inner)
            if m:
                attrs[key] = m.group(1).strip()
        attrs_by_ref[ref] = attrs
    return attrs_by_ref


def build_citations(
    cited_refs: Iterable[str],
    fetched_refs: Iterable[str],
    *,
    observation_categories: dict[str, str] | None = None,
    cite_attributes: dict[str, dict[str, str]] | None = None,
) -> tuple[Citation, ...]:
    """Build ratified Citation objects for refs in ``cited_refs``.

    Refs not present in ``fetched_refs`` are dropped — the verifier handles
    refusal logic for those upstream. ``observation_categories`` is an
    optional ``{fhir_ref: category}`` map so Observation rows route to the
    correct ``vitals`` vs ``labs`` chart card. ``cite_attributes`` carries
    the trailing ``<cite/>`` attributes (``source``, ``section``, etc.)
    so guideline chips can surface their source name and section.
    """

    fetched = set(fetched_refs)
    obs_cats = observation_categories or {}
    attrs_by_ref = cite_attributes or {}
    citations: list[Citation] = []
    seen: set[str] = set()
    for ref in cited_refs:
        if ref in seen or ref not in fetched:
            continue
        seen.add(ref)
        card: CitationCard = fhir_ref_to_card(
            ref, observation_category=obs_cats.get(ref)
        )
        attrs = attrs_by_ref.get(ref, {})
        citations.append(
            Citation(
                card=card,
                label=_default_label_for(ref, card, attrs),
                fhir_ref=ref,
            )
        )
    return tuple(citations)


def _default_label_for(
    fhir_ref: str,
    card: CitationCard,
    attrs: dict[str, str] | None = None,
) -> str:
    """Render a chip label for ``fhir_ref``.

    Guideline refs use the ``source`` / ``section`` attributes from the
    original ``<cite/>`` tag when present so the chip identifies the
    guideline by name rather than by opaque chunk id.
    """
    a = attrs or {}
    if fhir_ref.startswith("guideline:"):
        source = (a.get("source") or "").strip()
        section = (a.get("section") or "").strip()
        if source and section:
            return f"{source} · {section}"
        if source:
            return source
        if section:
            return f"Guideline · {section}"
        return "Guideline source"
    resource_type = fhir_ref.split("/", 1)[0] if "/" in fhir_ref else fhir_ref
    return f"{resource_type} ({card})"


def plain_block_from_text(
    text: str,
    *,
    citations: tuple[Citation, ...] = (),
    followups: tuple[str, ...] = (),
) -> PlainBlock:
    """Build a PlainBlock with cite-tags stripped from the lead."""

    return PlainBlock(
        lead=strip_cite_tags(text) or text,
        citations=citations,
        followups=followups,
    )


# ---------------------------------------------------------------------------
# Structured synthesis prompts
# ---------------------------------------------------------------------------

_TRIAGE_STRUCT_SYSTEM = """\
You are converting a triage cohort summary into a structured JSON object.

Input: a free-text triage brief and the list of FHIR resource refs that were
fetched this turn. Output: ONE JSON object with fields:
  lead: string (one sentence describing the panel state)
  cohort: array of {id, name, age, room, score, trend, reasons[], self, fhir_ref}
    - score: 0..100 NEWS2-style severity; if you can't compute one, estimate
      from the count signal (count>=2 → 70+, single high-acuity event → 80+,
      otherwise <50).
    - trend: "up" | "down" | "flat"
    - reasons: 1..3 short strings
    - self: true for the patient currently in scope; false otherwise
    - fhir_ref: "Patient/<id>" or null
  followups: optional array of suggested next-utterance strings

Order ``cohort`` from highest priority to lowest. Use ONLY patient ids
mentioned in the input text — do not invent.
"""

_OVERNIGHT_STRUCT_SYSTEM = """\
You are converting a per-patient overnight brief into a structured JSON object.

Input: a free-text brief about a single patient, plus the fetched FHIR refs.
Output: ONE JSON object with fields:
  lead: string (one sentence summarizing the most clinically significant event)
  deltas: 0..6 of {label, from, to, dir} — the most significant vitals/lab
    swings. ``dir`` is "up" | "down" | "flat".
  timeline: array of {t, kind, text, fhir_ref} — chronological ASCENDING.
    kind ∈ {Lab, Order, Med admin, Nursing note, Imaging, Vital, Other}.
    t is "HH:MM" wall clock OR ISO 8601.
  followups: optional array of suggested next-utterance strings

Use ONLY refs/values present in the input. If a value isn't on file, surface
it as "[not on file]" in the relevant string — never invent.
"""


async def synthesize_triage_block(
    model: BaseChatModel,
    *,
    synthesis_text: str,
    fetched_refs: list[str],
    active_patient_id: str | None,
) -> TriageBlock | PlainBlock:
    """Convert a triage synthesis into a TriageBlock; fall back to PlainBlock."""

    prompt = (
        f"FETCHED REFS: {fetched_refs}\n"
        f"ACTIVE PATIENT (self): {active_patient_id or '<none>'}\n\n"
        f"FREE-TEXT BRIEF:\n{synthesis_text}\n"
    )
    structured_model = model.with_structured_output(_TriageStructured)
    try:
        decision = await structured_model.ainvoke(
            [
                SystemMessage(content=_TRIAGE_STRUCT_SYSTEM),
                HumanMessage(content=prompt),
            ]
        )
    except (ValidationError, json.JSONDecodeError) as exc:  # noqa: PERF203
        _log.warning("triage structured-output validation failed: %s", exc)
        return plain_block_from_text(synthesis_text)
    except Exception as exc:  # noqa: BLE001 — never let LLM glitches break the wire
        _log.warning("triage structured-output call failed: %s", exc)
        return plain_block_from_text(synthesis_text)

    fetched = set(fetched_refs)
    cohort = tuple(
        CohortPatient(
            id=row.id,
            name=row.name,
            age=row.age,
            room=row.room,
            score=max(0, min(100, row.score)),
            trend=_normalize_dir(row.trend),
            reasons=tuple(row.reasons),
            is_self=bool(row.is_self),
            fhir_ref=row.fhir_ref,
        )
        for row in decision.cohort
    )
    cited = extract_cite_refs(synthesis_text)
    citations = build_citations(cited, fetched)
    followups = _materialize_triage_followups(cohort)
    return TriageBlock(
        lead=strip_cite_tags(decision.lead) or decision.lead,
        cohort=cohort,
        citations=citations,
        followups=followups,
    )


async def synthesize_overnight_block(
    model: BaseChatModel,
    *,
    synthesis_text: str,
    fetched_refs: list[str],
    observation_categories: dict[str, str] | None = None,
) -> OvernightBlock | PlainBlock:
    """Convert a per-patient overnight synthesis into an OvernightBlock."""

    prompt = (
        f"FETCHED REFS: {fetched_refs}\n\n"
        f"FREE-TEXT BRIEF:\n{synthesis_text}\n"
    )
    structured_model = model.with_structured_output(_OvernightStructured)
    try:
        decision = await structured_model.ainvoke(
            [
                SystemMessage(content=_OVERNIGHT_STRUCT_SYSTEM),
                HumanMessage(content=prompt),
            ]
        )
    except (ValidationError, json.JSONDecodeError) as exc:
        _log.warning("overnight structured-output validation failed: %s", exc)
        return plain_block_from_text(synthesis_text)
    except Exception as exc:  # noqa: BLE001
        _log.warning("overnight structured-output call failed: %s", exc)
        return plain_block_from_text(synthesis_text)

    deltas = tuple(
        Delta(label=d.label, from_=d.from_, to=d.to, dir=_normalize_dir(d.dir))
        for d in decision.deltas
    )
    timeline = tuple(
        TimelineEvent(
            t=t.t,
            kind=_normalize_timeline_kind(t.kind),
            text=t.text,
            fhir_ref=t.fhir_ref,
        )
        for t in decision.timeline
    )
    cited = extract_cite_refs(synthesis_text)
    citations = build_citations(
        cited, fetched_refs, observation_categories=observation_categories
    )
    return OvernightBlock(
        lead=strip_cite_tags(decision.lead) or decision.lead,
        deltas=deltas,
        timeline=timeline,
        citations=citations,
        followups=_OVERNIGHT_FOLLOWUPS,
    )


def block_from_clarify_text(text: str) -> PlainBlock:
    """Wrap a clarification question in a PlainBlock."""

    return plain_block_from_text(text)


def refusal_plain_block(text: str) -> PlainBlock:
    """Verifier refusal — strict PlainBlock, no followups."""

    return PlainBlock(lead=text, citations=(), followups=())


# ---------------------------------------------------------------------------
# Internal structured-output schemas (LLM target)
# ---------------------------------------------------------------------------

# These are looser than the wire DTOs — we accept what the LLM returns and
# normalize before converting to the strict frozen wire types above. Using
# Pydantic here lets ``with_structured_output`` validate the LLM's JSON.

from pydantic import BaseModel as _BaseModel  # noqa: E402  (separating concerns)
from pydantic import Field as _Field  # noqa: E402


class _CohortRowStructured(_BaseModel):
    """Loose LLM-side schema. ``self`` is aliased because Pydantic v2 forbids
    using the reserved Python identifier as a field name.
    """

    model_config = {"populate_by_name": True}

    id: str
    name: str
    age: int = 0
    room: str = ""
    score: int = 0
    trend: str = "flat"
    reasons: list[str] = _Field(default_factory=list)
    is_self: bool = _Field(default=False, alias="self")
    fhir_ref: str | None = None


class _TriageStructured(_BaseModel):
    lead: str
    cohort: list[_CohortRowStructured] = _Field(default_factory=list)
    followups: list[str] = _Field(default_factory=list)


class _DeltaStructured(_BaseModel):
    label: str
    from_: str = _Field(alias="from")
    to: str
    dir: str = "flat"

    model_config = {"populate_by_name": True}


class _TimelineStructured(_BaseModel):
    t: str
    kind: str = "Other"
    text: str
    fhir_ref: str | None = None


class _OvernightStructured(_BaseModel):
    lead: str
    deltas: list[_DeltaStructured] = _Field(default_factory=list)
    timeline: list[_TimelineStructured] = _Field(default_factory=list)
    followups: list[str] = _Field(default_factory=list)


def _materialize_triage_followups(
    cohort: tuple[CohortPatient, ...],
) -> tuple[str, ...]:
    """Materialize the canonical triage followup strings.

    Drop entries whose placeholders cannot be filled — empty templated chips
    are worse than no chip at all.
    """

    if not cohort:
        return ()
    self_patient = next((p for p in cohort if p.is_self), cohort[0])
    next_patient = next(
        (p for p in cohort if p.id != self_patient.id),
        None,
    )
    out: list[str] = []
    for template in _TRIAGE_FOLLOWUPS:
        if "{patient_name}" in template and self_patient.name:
            out.append(template.format(patient_name=self_patient.name))
        elif "{next_patient_name}" in template:
            if next_patient and next_patient.name:
                out.append(template.format(next_patient_name=next_patient.name))
        elif "{" not in template:
            out.append(template)
    return tuple(out)


_VALID_DIRS = {"up", "down", "flat"}
_VALID_TIMELINE_KINDS = {
    "Lab",
    "Order",
    "Med admin",
    "Nursing note",
    "Imaging",
    "Vital",
    "Other",
}


def _normalize_dir(value: str | None) -> str:
    """Coerce LLM-emitted direction labels to the contract enum."""

    if value is None:
        return "flat"
    canonical = value.strip().lower()
    if canonical in _VALID_DIRS:
        return canonical
    return "flat"


def _normalize_timeline_kind(value: str | None) -> str:
    """Coerce LLM-emitted timeline kinds to the contract enum."""

    if not value:
        return "Other"
    # LLM may casefold or use plurals; do a case-insensitive match.
    lookup = {k.lower(): k for k in _VALID_TIMELINE_KINDS}
    return lookup.get(value.strip().lower(), "Other")


