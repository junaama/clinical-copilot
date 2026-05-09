"""Deterministic DOCX referral-letter extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from .schemas import ReferralExtraction, ReferralLab, SourceCitation

_WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class _Paragraph:
    number: int
    text: str


def parse_docx_referral(
    file_data: bytes,
    *,
    document_id: str,
    extraction_model_name: str = "docx-referral-deterministic-parser",
) -> ReferralExtraction:
    """Parse a typed referral DOCX into a referral-specific extraction.

    The week-2 referral assets are structured letters with stable
    paragraph-level headings. We preserve those paragraph numbers in
    citations so downstream UI/chat can say exactly where a value came
    from without treating the document as a visual page.
    """

    paragraphs = _read_docx_paragraphs(file_data)
    if not paragraphs:
        msg = "DOCX referral contains no readable paragraphs"
        raise ValueError(msg)

    date_idx = _first_index_matching(paragraphs, r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
    re_para = _first_with_prefix(paragraphs, "RE:")
    reason_para = _first_with_prefix(paragraphs, "Reason for Referral:")
    history_para = _first_with_prefix(paragraphs, "History of Present Illness:")
    requested_para = _first_with_prefix(paragraphs, "Specific Question / Requested Action:")

    pmh = _section_items(paragraphs, "Past Medical History:", _SECTION_BOUNDARIES)
    medications = _section_items(paragraphs, "Current Medications:", _SECTION_BOUNDARIES)
    labs = _parse_labs(
        _section_paragraphs(paragraphs, "Pertinent Labs:", _SECTION_BOUNDARIES),
        document_id=document_id,
    )
    allergies = _parse_allergies(_first_with_prefix(paragraphs, "Allergies:"))
    patient_name, patient_dob, identifiers = _parse_patient_line(re_para.text if re_para else "")

    citations: dict[str, SourceCitation] = {}
    if reason_para is not None:
        citations["reason_for_referral"] = _citation(
            document_id,
            reason_para,
            "reason_for_referral",
            _strip_prefix(reason_para.text, "Reason for Referral:"),
        )
    if history_para is not None:
        citations["pertinent_history"] = _citation(
            document_id,
            history_para,
            "pertinent_history",
            _strip_prefix(history_para.text, "History of Present Illness:"),
        )
    if requested_para is not None:
        citations["requested_action"] = _citation(
            document_id,
            requested_para,
            "requested_action",
            _strip_prefix(requested_para.text, "Specific Question / Requested Action:"),
        )

    return ReferralExtraction(
        referring_provider=_provider_after_sincerely(paragraphs),
        referring_organization=paragraphs[0].text if paragraphs else None,
        receiving_provider=_paragraph_after_index(paragraphs, date_idx),
        receiving_organization=_paragraph_after_index(
            paragraphs,
            None if date_idx is None else date_idx + 1,
        ),
        patient_name=patient_name,
        patient_dob=patient_dob,
        patient_identifiers=identifiers,
        reason_for_referral=_strip_prefix(reason_para.text, "Reason for Referral:")
        if reason_para
        else None,
        pertinent_history=_strip_prefix(history_para.text, "History of Present Illness:")
        if history_para
        else None,
        past_medical_history=[p.text for p in pmh],
        current_medications=[p.text for p in medications],
        allergies=allergies,
        pertinent_labs=labs,
        requested_action=_strip_prefix(
            requested_para.text,
            "Specific Question / Requested Action:",
        )
        if requested_para
        else None,
        source_citations=citations,
        source_document_id=document_id,
        extraction_model=extraction_model_name,
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


_SECTION_BOUNDARIES: tuple[str, ...] = (
    "Reason for Referral:",
    "History of Present Illness:",
    "Past Medical History:",
    "Current Medications:",
    "Allergies:",
    "Pertinent Labs:",
    "Specific Question / Requested Action:",
    "Sincerely,",
    "Synthetic data",
)


def _read_docx_paragraphs(file_data: bytes) -> list[_Paragraph]:
    try:
        with ZipFile(BytesIO(file_data)) as archive:
            document_xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError) as exc:
        msg = "invalid DOCX referral archive"
        raise ValueError(msg) from exc

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        msg = "invalid DOCX document XML"
        raise ValueError(msg) from exc

    paragraphs: list[_Paragraph] = []
    for number, para in enumerate(root.findall(".//w:p", _WORD_NS), start=1):
        text = "".join(t.text or "" for t in para.findall(".//w:t", _WORD_NS)).strip()
        if text:
            paragraphs.append(_Paragraph(number=number, text=text))
    return paragraphs


def _first_index_matching(paragraphs: list[_Paragraph], pattern: str) -> int | None:
    compiled = re.compile(pattern)
    for index, para in enumerate(paragraphs):
        if compiled.search(para.text):
            return index
    return None


def _paragraph_after_index(paragraphs: list[_Paragraph], index: int | None) -> str | None:
    if index is None:
        return None
    next_index = index + 1
    if next_index >= len(paragraphs):
        return None
    return paragraphs[next_index].text


def _first_with_prefix(paragraphs: list[_Paragraph], prefix: str) -> _Paragraph | None:
    for para in paragraphs:
        if para.text.startswith(prefix):
            return para
    return None


def _strip_prefix(text: str, prefix: str) -> str:
    if text.startswith(prefix):
        return text[len(prefix):].strip()
    return text.strip()


def _section_paragraphs(
    paragraphs: list[_Paragraph],
    heading: str,
    boundaries: tuple[str, ...],
) -> list[_Paragraph]:
    start: int | None = None
    for index, para in enumerate(paragraphs):
        if para.text == heading:
            start = index + 1
            break
        if para.text.startswith(heading):
            return [para]
    if start is None:
        return []

    items: list[_Paragraph] = []
    for para in paragraphs[start:]:
        if any(para.text.startswith(boundary) for boundary in boundaries):
            break
        items.append(para)
    return items


def _section_items(
    paragraphs: list[_Paragraph],
    heading: str,
    boundaries: tuple[str, ...],
) -> list[_Paragraph]:
    return [
        para
        for para in _section_paragraphs(paragraphs, heading, boundaries)
        if not para.text.startswith(heading)
    ]


def _parse_patient_line(line: str) -> tuple[str | None, str | None, dict[str, str]]:
    value = _strip_prefix(line, "RE:")
    if not value:
        return None, None, {}
    parts = [part.strip() for part in value.split("|")]
    name = parts[0] if parts else None
    dob: str | None = None
    identifiers: dict[str, str] = {}
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, raw_value = [chunk.strip() for chunk in part.split(":", 1)]
        if key.upper() == "DOB":
            dob = raw_value
        elif key:
            identifiers[key.upper()] = raw_value
    return name, dob, identifiers


def _parse_allergies(para: _Paragraph | None) -> list[str]:
    if para is None:
        return []
    value = _strip_prefix(para.text, "Allergies:")
    if not value:
        return []
    return [item.strip() for item in re.split(r";|,", value) if item.strip()]


def _parse_labs(paragraphs: list[_Paragraph], *, document_id: str) -> list[ReferralLab]:
    labs: list[ReferralLab] = []
    for para in paragraphs:
        if para.text.startswith("Pertinent Labs:"):
            continue
        parsed = _parse_lab_line(para.text)
        if parsed is None:
            continue
        name, value, unit, flag, collection_date = parsed
        labs.append(
            ReferralLab(
                name=name,
                value=value,
                unit=unit,
                flag=flag,
                collection_date=collection_date,
                source_citation=_citation(
                    document_id,
                    para,
                    f"pertinent_labs[{len(labs)}]",
                    para.text,
                ),
            )
        )
    return labs


def _parse_lab_line(text: str) -> tuple[str, str, str | None, str | None, str | None] | None:
    if ":" not in text:
        return None
    name, rest = [chunk.strip() for chunk in text.split(":", 1)]
    if not name or not rest:
        return None

    collection_date: str | None = None
    date_match = re.search(r"\((\d{4}-\d{2}-\d{2})\)\s*$", rest)
    if date_match:
        collection_date = date_match.group(1)
        rest = rest[: date_match.start()].strip()

    flag: str | None = None
    flag_match = re.search(r"\[([^\]]+)\]", rest)
    if flag_match:
        flag = flag_match.group(1).strip()
        rest = (rest[: flag_match.start()] + rest[flag_match.end() :]).strip()

    pieces = rest.split(maxsplit=1)
    value = pieces[0]
    unit = pieces[1].strip() if len(pieces) > 1 and pieces[1].strip() else None
    return name, value, unit, flag, collection_date


def _provider_after_sincerely(paragraphs: list[_Paragraph]) -> str | None:
    for index, para in enumerate(paragraphs):
        if para.text == "Sincerely,":
            return _paragraph_after_index(paragraphs, index)
    return None


def _citation(
    document_id: str,
    para: _Paragraph,
    field: str,
    quote: str,
) -> SourceCitation:
    return SourceCitation(
        source_type="docx_referral",
        source_id=document_id,
        page_or_section=f"paragraph {para.number}",
        field_or_chunk_id=field,
        quote_or_value=quote,
    )
