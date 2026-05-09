"""Deterministic HL7 v2 ADT parser.

Maps ADT trigger events (notably ``A08`` update-patient-info, but the
parser is segment-driven so ``A01`` / ``A04`` / ``A28`` / ``A31`` flow
through the same path) into :class:`AdtExtraction`. Like the ORU parser,
this stays at the ER7 subset used by the week-2 asset pack: pipe-
separated fields, caret components, tilde repetitions, ``\r`` segment
separators. No LLM call.

Citations name the (segment, set_id, field) triple so the supervisor
and UI can link any extracted fact back to the literal HL7 location.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .schemas import (
    AdtAllergy,
    AdtContact,
    AdtExtraction,
    AdtGuarantor,
    AdtInsurance,
    AdtMessageMetadata,
    AdtPatientDemographics,
    AdtPrimaryCare,
    AdtSegmentCitation,
    AdtVisit,
)

_SEGMENT_SPLIT = re.compile(r"\r\n|\n|\r")
_HL7_DATETIME_FORMATS: dict[int, str] = {
    4: "%Y",
    6: "%Y%m",
    8: "%Y%m%d",
    10: "%Y%m%d%H",
    12: "%Y%m%d%H%M",
    14: "%Y%m%d%H%M%S",
}


def parse_hl7_adt(file_data: bytes, *, document_id: str) -> AdtExtraction:
    """Parse an HL7 v2 ADT message into the ADT extraction shape.

    Raises ``ValueError`` for non-HL7 input or non-ADT messages — the
    upload route turns that into a user-safe ``extraction_failed``
    response rather than letting a parser exception leak to the wire.
    """

    text = _decode_hl7(file_data)
    raw_segments = [line for line in _SEGMENT_SPLIT.split(text.strip()) if line]
    if not raw_segments or not raw_segments[0].startswith("MSH"):
        msg = "not an HL7 message"
        raise ValueError(msg)

    segments = [_split_segment(line) for line in raw_segments]
    msh = _first_segment(segments, "MSH")
    if msh is None or _field(msh, 9).split("^", 1)[0] != "ADT":
        msg = "not an ADT message"
        raise ValueError(msg)

    citations: list[AdtSegmentCitation] = []
    metadata = _build_metadata(msh, _first_segment(segments, "EVN"), document_id, citations)
    pid = _first_segment(segments, "PID")
    identifiers = _patient_identifiers(_field(pid, 3) if pid else "")
    if identifiers:
        citations.append(
            _citation(document_id, "PID", "1", "PID-3", _field(pid, 3) if pid else None)
        )
    demographics = _build_demographics(pid, document_id, citations)
    primary_care = _build_primary_care(_first_segment(segments, "PD1"), document_id, citations)
    visit = _build_visit(_first_segment(segments, "PV1"), document_id, citations)

    contacts = [
        contact
        for contact in (
            _build_contact(seg, document_id, citations)
            for seg in segments
            if seg and seg[0] == "NK1"
        )
        if contact is not None
    ]
    allergies = [
        allergy
        for allergy in (
            _build_allergy(seg, document_id, citations)
            for seg in segments
            if seg and seg[0] == "AL1"
        )
        if allergy is not None
    ]
    guarantor = _build_guarantor(_first_segment(segments, "GT1"), document_id, citations)
    insurance = [
        plan
        for plan in (
            _build_insurance(seg, document_id, citations)
            for seg in segments
            if seg and seg[0] == "IN1"
        )
        if plan is not None
    ]

    return AdtExtraction(
        message_metadata=metadata,
        patient_identifiers=identifiers,
        patient_demographics=demographics,
        primary_care=primary_care,
        visit=visit,
        contacts=contacts,
        allergies=allergies,
        guarantor=guarantor,
        insurance=insurance,
        citations=citations,
        source_document_id=document_id,
        extraction_model="hl7-adt-deterministic-parser",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Segment builders
# ---------------------------------------------------------------------------


def _build_metadata(
    msh: list[str],
    evn: list[str] | None,
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtMessageMetadata:
    message_type_field = _field(msh, 9)
    msg_type = _component(message_type_field, 0) or None
    trigger = _component(message_type_field, 1) or None
    structure = _component(message_type_field, 2) or None
    metadata = AdtMessageMetadata(
        sending_application=_component(_field(msh, 3), 0) or None,
        sending_facility=_component(_field(msh, 4), 0) or None,
        receiving_application=_component(_field(msh, 5), 0) or None,
        receiving_facility=_component(_field(msh, 6), 0) or None,
        message_type=msg_type,
        trigger_event=trigger,
        message_structure=structure,
        message_control_id=_field(msh, 10) or None,
        message_datetime=_hl7_datetime(_field(msh, 7)),
        processing_id=_component(_field(msh, 11), 0) or None,
        version=_field(msh, 12) or None,
        event_type=_field(evn, 1) or None if evn else None,
        event_datetime=_hl7_datetime(_field(evn, 2)) if evn else None,
        event_reason=_field(evn, 6) or None if evn else None,
    )
    citations.append(_citation(document_id, "MSH", None, "MSH-9", message_type_field))
    if evn is not None:
        citations.append(_citation(document_id, "EVN", None, "EVN-1", _field(evn, 1)))
    return metadata


def _build_demographics(
    pid: list[str] | None,
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtPatientDemographics:
    if pid is None:
        return AdtPatientDemographics()
    name = _person_name(_field(pid, 5))
    dob = _hl7_datetime(_field(pid, 7))
    gender = _field(pid, 8) or None
    race = _component(_field(pid, 10), 1) or None
    address = _format_address(_field(pid, 11))
    phone = _format_phone(_field(pid, 13))
    marital_status = _component(_field(pid, 16), 0) or None
    language = _component(_field(pid, 15), 0) or None
    ethnicity = _component(_field(pid, 22), 1) or None
    citations.append(_citation(document_id, "PID", "1", "PID-5", _field(pid, 5)))
    return AdtPatientDemographics(
        name=name,
        dob=dob,
        gender=gender,
        race=race,
        ethnicity=ethnicity,
        marital_status=marital_status,
        address=address,
        phone=phone,
        language=language,
    )


def _build_primary_care(
    pd1: list[str] | None,
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtPrimaryCare | None:
    if pd1 is None:
        return None
    facility = _component(_field(pd1, 3), 0) or None
    provider = _person_name_xcn(_field(pd1, 4))
    if facility is None and provider is None:
        return None
    citations.append(_citation(document_id, "PD1", None, "PD1-4", _field(pd1, 4)))
    return AdtPrimaryCare(
        patient_primary_facility=facility,
        patient_primary_care_provider=provider,
    )


def _build_visit(
    pv1: list[str] | None,
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtVisit | None:
    if pv1 is None:
        return None
    patient_class = _field(pv1, 2) or None
    location = _format_location(_field(pv1, 3))
    attending = _person_name_xcn(_field(pv1, 7))
    referring = _person_name_xcn(_field(pv1, 8))
    visit_number = _component(_field(pv1, 19), 0) or None
    admission_datetime = _hl7_datetime(_field(pv1, 44))
    if all(
        value is None
        for value in (
            patient_class,
            location,
            attending,
            referring,
            visit_number,
            admission_datetime,
        )
    ):
        return None
    citations.append(
        _citation(
            document_id,
            "PV1",
            _field(pv1, 1) or None,
            "PV1-7",
            _field(pv1, 7),
        )
    )
    return AdtVisit(
        patient_class=patient_class,
        location=location,
        attending_provider=attending,
        referring_provider=referring,
        admission_datetime=admission_datetime,
        visit_number=visit_number,
    )


def _build_contact(
    nk1: list[str],
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtContact | None:
    name = _person_name(_field(nk1, 2))
    relationship = _component(_field(nk1, 3), 0) or None
    phone = _format_phone(_field(nk1, 5))
    address = _format_address(_field(nk1, 4))
    if all(value is None for value in (name, relationship, phone, address)):
        return None
    citations.append(
        _citation(
            document_id,
            "NK1",
            _field(nk1, 1) or None,
            "NK1-2",
            _field(nk1, 2),
        )
    )
    return AdtContact(
        name=name,
        relationship=relationship,
        phone=phone,
        address=address,
    )


def _build_allergy(
    al1: list[str],
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtAllergy | None:
    substance_field = _field(al1, 3)
    substance = _component(substance_field, 1) or _component(substance_field, 0)
    if not substance:
        return None
    citations.append(
        _citation(
            document_id,
            "AL1",
            _field(al1, 1) or None,
            "AL1-3",
            substance_field,
        )
    )
    return AdtAllergy(
        type=_component(_field(al1, 2), 0) or None,
        substance=substance,
        severity=_component(_field(al1, 4), 0) or None,
        reaction=_field(al1, 5) or None,
    )


def _build_guarantor(
    gt1: list[str] | None,
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtGuarantor | None:
    if gt1 is None:
        return None
    # Address/phone/relationship indices in the week-2 fixtures land at
    # GT1-6 / GT1-7 / GT1-10 rather than the strict v2.5.1 positions
    # (GT1-5 / GT1-6 / GT1-11). The fixture layout is what we ingest;
    # callers can refine if real-world senders differ later.
    name = _person_name(_field(gt1, 3))
    address = _format_address(_field(gt1, 6))
    phone = _format_phone(_field(gt1, 7))
    relationship = _component(_field(gt1, 10), 0) or None
    if all(value is None for value in (name, address, phone, relationship)):
        return None
    citations.append(
        _citation(
            document_id,
            "GT1",
            _field(gt1, 1) or None,
            "GT1-3",
            _field(gt1, 3),
        )
    )
    return AdtGuarantor(
        name=name,
        address=address,
        phone=phone,
        relationship_to_patient=relationship,
    )


def _build_insurance(
    in1: list[str],
    document_id: str,
    citations: list[AdtSegmentCitation],
) -> AdtInsurance | None:
    # Field positions follow the week-2 fixture layout, which keeps the
    # standard positions for IN1-2/3/4/16/17/19/22 but slides
    # group-number, member-id, and plan-type by one slot
    # (IN1-9 / IN1-35 / IN1-43 instead of the v2.5.1 IN1-8 / IN1-36 /
    # IN1-47). We match the fixture so the smoke can assert real values
    # rather than nulls.
    plan_id = _field(in1, 2) or None
    company_id = _component(_field(in1, 3), 0) or None
    company_name = _component(_field(in1, 4), 0) or None
    group_number = _field(in1, 9) or None
    group_name = _field(in1, 10) or None
    insured_name = _person_name(_field(in1, 16))
    relationship = _component(_field(in1, 17), 0) or None
    member_id = _field(in1, 35) or None
    plan_type = _field(in1, 43) or None
    if all(
        value is None
        for value in (
            plan_id,
            company_id,
            company_name,
            group_number,
            insured_name,
            member_id,
            plan_type,
        )
    ):
        return None
    citations.append(
        _citation(
            document_id,
            "IN1",
            _field(in1, 1) or None,
            "IN1-4",
            _field(in1, 4),
        )
    )
    return AdtInsurance(
        plan_id=plan_id,
        company_id=company_id,
        company_name=company_name,
        group_number=group_number,
        group_name=group_name,
        insured_name=insured_name,
        relationship_to_subscriber=relationship,
        member_id=member_id,
        plan_type=plan_type,
    )


# ---------------------------------------------------------------------------
# Low-level field helpers (mirror hl7_oru.py for parser consistency)
# ---------------------------------------------------------------------------


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
        # MSH is 1-indexed and the field separator counts as MSH-1; the
        # field encoding characters are MSH-2. Our split() treats the
        # leading ``MSH`` as index 0 and ``^~\&`` as index 1, so MSH-3
        # already lands at index 2 — i.e. ``field_number - 1``.
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


def _person_name(value: str) -> str | None:
    """Render an HL7 XPN/CX person-name field as ``Given Middle Family``.

    Returns ``None`` when the field is empty or carries no usable parts.
    """
    if not value:
        return None
    family = _component(value, 0).title()
    given = _component(value, 1).title()
    middle = _component(value, 2).title()
    rendered = " ".join(part for part in (given, middle, family) if part)
    return rendered or None


def _person_name_xcn(value: str) -> str | None:
    """Render an HL7 XCN provider-name field as ``Given Middle Family``.

    XCN places the id in component 1 (so PV1-7 looks like
    ``1618829315^PARK^HELEN^M^…``); family/given/middle slide right by
    one component compared to XPN/PID-5.
    """
    if not value:
        return None
    family = _component(value, 1).title()
    given = _component(value, 2).title()
    middle = _component(value, 3).title()
    rendered = " ".join(part for part in (given, middle, family) if part)
    return rendered or None


def _format_address(value: str) -> str | None:
    """Concat HL7 XAD components into a single human-readable address line."""
    if not value:
        return None
    street = _component(value, 0)
    other_designation = _component(value, 1)
    city = _component(value, 2)
    state = _component(value, 3)
    postal = _component(value, 4)
    country = _component(value, 5)
    line1 = " ".join(part for part in (street, other_designation) if part)
    locality = ", ".join(part for part in (city, state) if part)
    if postal:
        locality = f"{locality} {postal}".strip()
    address = ", ".join(part for part in (line1, locality, country) if part)
    return address or None


def _format_phone(value: str) -> str | None:
    """Render the HL7 XTN phone repetition as ``(area) prefix-line``.

    XTN: ``^PRN^PH^^^510^5550142``. We use components 5 (area code) and
    6 (local number) when present; otherwise return component 0 (the
    legacy formatted number) verbatim. ``~``-separated repetitions are
    folded — only the first repetition is rendered.
    """
    if not value:
        return None
    first = value.split("~", 1)[0].strip()
    if not first:
        return None
    area = _component(first, 5)
    local = _component(first, 6)
    if area and local:
        return f"({area}) {local[:3]}-{local[3:]}" if len(local) >= 7 else f"({area}) {local}"
    legacy = _component(first, 0)
    return legacy or first


def _format_location(value: str) -> str | None:
    """Concat HL7 PL components into a single location string."""
    if not value:
        return None
    point_of_care = _component(value, 0)
    facility = _component(value, 3)
    parts = [part for part in (point_of_care, facility) if part]
    return " - ".join(parts) if parts else None


def _patient_identifiers(value: str) -> list[dict[str, str]]:
    """Split repeated PID-3 entries into ``{id, type, assigning_authority}`` rows."""
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


def _hl7_datetime(value: str) -> str | None:
    if not value:
        return None
    value = value.strip()
    fmt = _HL7_DATETIME_FORMATS.get(len(value))
    if fmt is None:
        return value
    try:
        return datetime.strptime(value, fmt).isoformat()
    except ValueError:
        return value


def _citation(
    document_id: str,
    segment: str,
    set_id: str | None,
    field: str | None,
    quote: Any,
) -> AdtSegmentCitation:
    quote_str: str | None
    if quote is None:
        quote_str = None
    else:
        quote_str = str(quote).strip() or None
    return AdtSegmentCitation(
        source_id=document_id,
        segment=segment,
        set_id=set_id,
        field=field,
        quote_or_value=quote_str,
    )
