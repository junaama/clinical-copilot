"""Deterministic HL7 v2 ORU-R01 lab extraction."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .schemas import LabExtraction, LabResult, SourceCitation

_SEGMENT_SPLIT = re.compile(r"\r\n|\n|\r")

_ABNORMAL_FLAG_MAP = {
    "H": "high",
    "L": "low",
    "HH": "critical_high",
    "LL": "critical_low",
    "N": "normal",
}


def parse_hl7_oru_lab(file_data: bytes, *, document_id: str) -> LabExtraction:
    """Parse an HL7 v2 ORU-R01 message into the lab extraction shape.

    The parser intentionally covers the ER7 subset used by the week-2 asset
    pack: MSH/PID/PV1/ORC/OBR/OBX/NTE segments with pipe-separated fields,
    caret components, and tilde repetitions. It does not call an LLM.
    """

    text = _decode_hl7(file_data)
    raw_segments = [line for line in _SEGMENT_SPLIT.split(text.strip()) if line]
    if not raw_segments or not raw_segments[0].startswith("MSH"):
        msg = "not an HL7 message"
        raise ValueError(msg)

    segments = [_split_segment(line) for line in raw_segments]
    msh = _first_segment(segments, "MSH")
    if msh is None or _field(msh, 9).split("^", 1)[0] != "ORU":
        msg = "not an ORU message"
        raise ValueError(msg)

    pid = _first_segment(segments, "PID")
    patient_name = _patient_name(_field(pid, 5) if pid else "")
    patient_identifiers = _patient_identifiers(_field(pid, 3) if pid else "")
    lab_name = _component(_field(msh, 4), 0) or None

    results: list[LabResult] = []
    notes: list[dict[str, str]] = []
    orders: list[dict[str, Any]] = []
    current_order: dict[str, Any] | None = None
    current_orc: list[str] | None = None
    current_obr: list[str] | None = None

    for segment in segments:
        seg_name = segment[0]
        if seg_name == "ORC":
            current_orc = segment
            continue
        if seg_name == "OBR":
            current_obr = segment
            current_order = _order_context(current_orc, current_obr)
            orders.append(current_order)
            continue
        if seg_name == "OBX":
            if current_obr is None:
                continue
            results.append(
                _obx_to_lab_result(
                    segment,
                    current_obr=current_obr,
                    document_id=document_id,
                )
            )
            continue
        if seg_name == "NTE":
            note = _nte_note(segment)
            notes.append(note)
            if current_order is not None:
                order_notes = current_order.setdefault("notes", [])
                if isinstance(order_notes, list):
                    order_notes.append(note)

    first_order = orders[0] if orders else None
    collection_date = _first_present(
        (result.collection_date for result in results),
        fallback=_hl7_datetime(_field(_first_segment(segments, "OBR"), 7)),
    )

    return LabExtraction(
        patient_name=patient_name,
        collection_date=collection_date,
        ordering_provider=_provider_name(
            str(first_order.get("ordering_provider_raw", "")) if first_order else ""
        ),
        lab_name=lab_name,
        patient_identifiers=patient_identifiers,
        order_context=first_order,
        orders=orders,
        notes=notes,
        results=results,
        source_document_id=document_id,
        extraction_model="hl7-oru-deterministic-parser",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


def _decode_hl7(file_data: bytes) -> str:
    try:
        return file_data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return file_data.decode("latin-1")


def _split_segment(line: str) -> list[str]:
    return line.rstrip("\r\n").split("|")


def _first_segment(segments: list[list[str]], name: str) -> list[str] | None:
    return next((segment for segment in segments if segment and segment[0] == name), None)


def _field(segment: list[str] | None, field_number: int) -> str:
    if segment is None:
        return ""
    if segment[0] == "MSH":
        index = field_number - 1
    else:
        index = field_number
    if index < 0 or index >= len(segment):
        return ""
    return segment[index]


def _component(value: str, index: int) -> str:
    parts = value.split("^")
    if index >= len(parts):
        return ""
    return parts[index].strip()


def _patient_name(value: str) -> str | None:
    if not value:
        return None
    family = _component(value, 0).title()
    given = _component(value, 1).title()
    middle = _component(value, 2).title()
    return " ".join(part for part in (given, middle, family) if part) or None


def _provider_name(value: str) -> str | None:
    if not value:
        return None
    family = _component(value, 1).title()
    given = _component(value, 2).title()
    middle = _component(value, 3).title()
    return " ".join(part for part in (given, middle, family) if part) or None


def _coded_text(value: str) -> tuple[str | None, str | None, str | None]:
    if not value:
        return None, None, None
    return (
        _component(value, 0) or None,
        _component(value, 1) or None,
        _component(value, 2) or None,
    )


def _patient_identifiers(value: str) -> list[dict[str, str]]:
    identifiers: list[dict[str, str]] = []
    for repetition in value.split("~"):
        if not repetition:
            continue
        identifier = _component(repetition, 0)
        if not identifier:
            continue
        row = {"id": identifier}
        assigning_authority = _component(repetition, 3)
        identifier_type = _component(repetition, 4)
        if assigning_authority:
            row["assigning_authority"] = assigning_authority
        if identifier_type:
            row["type"] = identifier_type
        identifiers.append(row)
    return identifiers


def _order_context(
    current_orc: list[str] | None,
    current_obr: list[str],
) -> dict[str, Any]:
    code, text, coding_system = _coded_text(_field(current_obr, 4))
    order = {
        "placer_order_number": _field(current_obr, 2) or _field(current_orc, 2),
        "filler_order_number": _field(current_obr, 3) or _field(current_orc, 3),
        "universal_service_id": code,
        "universal_service_text": text,
        "universal_service_coding_system": coding_system,
        "observation_datetime": _hl7_datetime(_field(current_obr, 7)),
        "specimen_received_datetime": _hl7_datetime(_field(current_obr, 14)),
        "result_report_datetime": _hl7_datetime(_field(current_obr, 22)),
        "status": _field(current_obr, 25) or None,
        "ordering_provider_raw": _field(current_obr, 16) or _field(current_orc, 12),
    }
    return {key: value for key, value in order.items() if value not in (None, "")}


def _obx_to_lab_result(
    obx: list[str],
    *,
    current_obr: list[str],
    document_id: str,
) -> LabResult:
    loinc, test_name, _coding_system = _coded_text(_field(obx, 3))
    set_id = _field(obx, 1) or "?"
    value = _field(obx, 5)
    collection_date = _hl7_datetime(_field(obx, 14)) or _hl7_datetime(_field(current_obr, 7))
    return LabResult(
        test_name=test_name or loinc or "Unknown observation",
        loinc_code=loinc,
        value=value,
        unit=_component(_field(obx, 6), 0) or _field(obx, 6) or "unknown",
        reference_range=_field(obx, 7) or None,
        collection_date=collection_date,
        status=_field(obx, 11) or None,
        abnormal_flag=_abnormal_flag(_field(obx, 8)),
        confidence="high",
        source_citation=SourceCitation(
            source_type="hl7_oru",
            source_id=document_id,
            page_or_section=f"OBX[{set_id}]",
            field_or_chunk_id="OBX-5",
            quote_or_value=value,
        ),
    )


def _nte_note(segment: list[str]) -> dict[str, str]:
    note = {
        "segment": "NTE",
        "set_id": _field(segment, 1),
        "source": _field(segment, 2),
        "comment": _field(segment, 3),
    }
    return {key: value for key, value in note.items() if value}


def _abnormal_flag(value: str) -> str:
    first_flag = value.split("~", 1)[0].strip().upper()
    return _ABNORMAL_FLAG_MAP.get(first_flag, "unknown")


def _hl7_datetime(value: str) -> str | None:
    if not value:
        return None
    value = value.strip()
    formats = {
        4: "%Y",
        6: "%Y%m",
        8: "%Y%m%d",
        10: "%Y%m%d%H",
        12: "%Y%m%d%H%M",
        14: "%Y%m%d%H%M%S",
    }
    fmt = formats.get(len(value))
    if fmt is None:
        return value
    try:
        return datetime.strptime(value, fmt).isoformat()
    except ValueError:
        return value


def _first_present(values: Any, *, fallback: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return fallback
