"""Deterministic XLSX clinical workbook extraction."""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from .schemas import (
    LabExtraction,
    LabResult,
    SourceCitation,
    WorkbookCareGap,
    WorkbookExtraction,
    WorkbookLabTrend,
    WorkbookLabTrendValue,
    WorkbookMedication,
    WorkbookPatientField,
)

_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_ATTR = f"{{{_OFFICE_REL_NS}}}id"
_NS = {"s": _SPREADSHEET_NS, "r": _REL_NS}
_EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=UTC)


@dataclass(frozen=True)
class _Cell:
    ref: str
    value: str


@dataclass(frozen=True)
class _Sheet:
    name: str
    rows: list[list[_Cell]]


def parse_xlsx_workbook(
    file_data: bytes,
    *,
    document_id: str,
) -> tuple[WorkbookExtraction, LabExtraction]:
    """Parse a week-2 XLSX clinical workbook.

    The parser reads OOXML directly from the workbook zip. It recognizes the
    asset-pack sheet roles by normalized sheet name and header shape, then
    returns both a workbook-native extraction and a lab-compatible extraction
    generated from the lab-trend matrix.
    """

    try:
        sheets = _read_sheets(file_data)
    except (BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        raise ValueError("invalid XLSX workbook") from exc

    sheet_roles = _classify_sheets(sheets)
    if "patient" not in sheet_roles:
        raise ValueError("workbook missing patient sheet")

    patient_sheet = sheet_roles["patient"]
    patient_fields = _parse_patient_sheet(patient_sheet, document_id=document_id)
    medications = _parse_medications_sheet(
        sheet_roles.get("medications"), document_id=document_id
    )
    lab_trends = _parse_lab_trends_sheet(
        sheet_roles.get("lab_trends"), document_id=document_id
    )
    care_gaps = _parse_care_gaps_sheet(
        sheet_roles.get("care_gaps"), document_id=document_id
    )

    extraction_timestamp = datetime.now(UTC).isoformat()
    workbook = WorkbookExtraction(
        patient_fields=patient_fields,
        medications=medications,
        lab_trends=lab_trends,
        care_gaps=care_gaps,
        sheet_roles={role: sheet.name for role, sheet in sheet_roles.items()},
        source_document_id=document_id,
        extraction_model="xlsx-workbook-deterministic-parser",
        extraction_timestamp=extraction_timestamp,
    )
    return workbook, _lab_extraction_from_workbook(
        workbook,
        document_id=document_id,
        extraction_timestamp=extraction_timestamp,
    )


def _read_sheets(file_data: bytes) -> list[_Sheet]:
    with ZipFile(BytesIO(file_data)) as workbook_zip:
        shared_strings = _read_shared_strings(workbook_zip)
        workbook = ET.fromstring(workbook_zip.read("xl/workbook.xml"))
        relationships = ET.fromstring(workbook_zip.read("xl/_rels/workbook.xml.rels"))
        relationship_targets = {
            rel.get("Id"): rel.get("Target") for rel in relationships.findall("r:Relationship", _NS)
        }

        sheets: list[_Sheet] = []
        for sheet in workbook.findall("s:sheets/s:sheet", _NS):
            name = sheet.get("name") or ""
            relationship_id = sheet.get(_REL_ATTR)
            target = relationship_targets.get(relationship_id)
            if not target:
                continue
            sheet_path = _workbook_target_path(target)
            root = ET.fromstring(workbook_zip.read(sheet_path))
            rows = [
                _read_row(row, shared_strings)
                for row in root.findall(".//s:sheetData/s:row", _NS)
            ]
            sheets.append(_Sheet(name=name, rows=rows))
        return sheets


def _read_shared_strings(workbook_zip: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook_zip.namelist():
        return []
    root = ET.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("s:si", _NS):
        strings.append("".join(t.text or "" for t in item.findall(".//s:t", _NS)))
    return strings


def _workbook_target_path(target: str) -> str:
    path = target.lstrip("/")
    if not path.startswith("xl/"):
        path = posixpath.normpath(posixpath.join("xl", path))
    return path


def _read_row(row: ET.Element, shared_strings: list[str]) -> list[_Cell]:
    cells: list[_Cell] = []
    for cell in row.findall("s:c", _NS):
        ref = cell.get("r") or ""
        value = _cell_value(cell, shared_strings)
        cells.append(_Cell(ref=ref, value=value))
    return cells


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        inline = cell.find("s:is", _NS)
        if inline is None:
            return ""
        return "".join(t.text or "" for t in inline.findall(".//s:t", _NS)).strip()

    value = cell.find("s:v", _NS)
    if value is None or value.text is None:
        return ""
    raw = value.text.strip()
    if cell_type == "s":
        try:
            return shared_strings[int(raw)].strip()
        except (IndexError, ValueError):
            return raw
    return _normalize_number(raw)


def _normalize_number(raw: str) -> str:
    if re.fullmatch(r"-?\d+\.0+", raw):
        return raw.split(".", 1)[0]
    return raw


def _classify_sheets(sheets: list[_Sheet]) -> dict[str, _Sheet]:
    roles: dict[str, _Sheet] = {}
    for sheet in sheets:
        normalized = _normalize_header(sheet.name)
        headers = {_normalize_header(cell.value) for cell in _first_non_empty_row(sheet)}
        if normalized == "patient" or {"field", "value"}.issubset(headers):
            roles["patient"] = sheet
        elif "medication" in normalized or {"brand", "generic", "sig"}.issubset(headers):
            roles["medications"] = sheet
        elif "lab" in normalized or {"test", "loinc", "reference_range"}.issubset(headers):
            roles["lab_trends"] = sheet
        elif "gap" in normalized or {"measure", "status", "due_date"}.issubset(headers):
            roles["care_gaps"] = sheet
    return roles


def _first_non_empty_row(sheet: _Sheet) -> list[_Cell]:
    return next((row for row in sheet.rows if any(cell.value for cell in row)), [])


def _parse_patient_sheet(
    sheet: _Sheet,
    *,
    document_id: str,
) -> dict[str, WorkbookPatientField]:
    fields: dict[str, WorkbookPatientField] = {}
    for row in sheet.rows[1:]:
        if len(row) < 2 or not row[0].value or not row[1].value:
            continue
        fields[row[0].value] = WorkbookPatientField(
            value=row[1].value,
            source_citation=_citation(
                document_id=document_id,
                sheet_name=sheet.name,
                cell_ref=row[1].ref,
                quote_or_value=row[1].value,
            ),
        )
    return fields


def _parse_medications_sheet(
    sheet: _Sheet | None,
    *,
    document_id: str,
) -> list[WorkbookMedication]:
    if sheet is None or not sheet.rows:
        return []
    headers = _header_map(sheet.rows[0])
    medications: list[WorkbookMedication] = []
    for row in sheet.rows[1:]:
        values = _row_values(row, headers)
        if not any(values.values()):
            continue
        medications.append(
            WorkbookMedication(
                brand=values.get("brand"),
                generic=values.get("generic"),
                strength=values.get("strength"),
                route=values.get("route"),
                sig=values.get("sig"),
                indication=values.get("indication"),
                start_date=values.get("start_date"),
                last_filled=values.get("last_filled"),
                refills_remaining=values.get("refills_remaining"),
                prescriber=values.get("prescriber"),
                source_citation=_citation(
                    document_id=document_id,
                    sheet_name=sheet.name,
                    cell_ref=_row_range(row),
                    quote_or_value=_row_quote(row),
                ),
            )
        )
    return medications


def _parse_lab_trends_sheet(
    sheet: _Sheet | None,
    *,
    document_id: str,
) -> list[WorkbookLabTrend]:
    if sheet is None or not sheet.rows:
        return []
    header_cells = sheet.rows[0]
    headers = [_normalize_header(cell.value) for cell in header_cells]
    lab_trends: list[WorkbookLabTrend] = []
    for row in sheet.rows[1:]:
        if len(row) < 5 or not row[0].value:
            continue
        values: list[WorkbookLabTrendValue] = []
        reference_range = _cell_by_header(row, headers, "reference_range")
        for index, cell in enumerate(row):
            if index < 4 or not cell.value:
                continue
            collection_date = _header_value(header_cells, index)
            if not collection_date:
                continue
            values.append(
                WorkbookLabTrendValue(
                    collection_date=_date_like(collection_date),
                    value=cell.value,
                    abnormal_flag=_infer_abnormal_flag(cell.value, reference_range),
                    source_citation=_citation(
                        document_id=document_id,
                        sheet_name=sheet.name,
                        cell_ref=cell.ref,
                        quote_or_value=cell.value,
                    ),
                )
            )
        lab_trends.append(
            WorkbookLabTrend(
                test_name=row[0].value,
                loinc_code=_cell_by_header(row, headers, "loinc"),
                unit=_cell_by_header(row, headers, "units"),
                reference_range=reference_range,
                values=values,
                source_citation=_citation(
                    document_id=document_id,
                    sheet_name=sheet.name,
                    cell_ref=_row_range(row),
                    quote_or_value=_row_quote(row),
                ),
            )
        )
    return lab_trends


def _parse_care_gaps_sheet(
    sheet: _Sheet | None,
    *,
    document_id: str,
) -> list[WorkbookCareGap]:
    if sheet is None or not sheet.rows:
        return []
    headers = _header_map(sheet.rows[0])
    care_gaps: list[WorkbookCareGap] = []
    for row in sheet.rows[1:]:
        values = _row_values(row, headers)
        measure = values.get("measure")
        if not measure:
            continue
        care_gaps.append(
            WorkbookCareGap(
                measure=measure,
                reference=values.get("hedis_or_uspstf_ref"),
                status=values.get("status"),
                last_done=values.get("last_done"),
                due_date=values.get("due_date"),
                notes=values.get("notes"),
                source_citation=_citation(
                    document_id=document_id,
                    sheet_name=sheet.name,
                    cell_ref=_row_range(row),
                    quote_or_value=_row_quote(row),
                ),
            )
        )
    return care_gaps


def _lab_extraction_from_workbook(
    workbook: WorkbookExtraction,
    *,
    document_id: str,
    extraction_timestamp: str,
) -> LabExtraction:
    results: list[LabResult] = []
    for trend in workbook.lab_trends:
        for value in trend.values:
            results.append(
                LabResult(
                    test_name=trend.test_name,
                    loinc_code=trend.loinc_code,
                    value=value.value,
                    unit=trend.unit or "unknown",
                    reference_range=trend.reference_range,
                    collection_date=value.collection_date,
                    status=None,
                    abnormal_flag=value.abnormal_flag,
                    confidence="high",
                    source_citation=value.source_citation,
                )
            )

    return LabExtraction(
        patient_name=_patient_field(workbook, "Name"),
        collection_date=_latest_collection_date(results),
        ordering_provider=_patient_field(workbook, "PCP"),
        lab_name="XLSX clinical workbook",
        patient_identifiers=_patient_identifiers(workbook),
        order_context={"source_sheet": workbook.sheet_roles.get("lab_trends", "Labs_Trend")},
        results=results,
        source_document_id=document_id,
        extraction_model="xlsx-workbook-deterministic-parser",
        extraction_timestamp=extraction_timestamp,
    )


def _header_map(row: list[_Cell]) -> dict[int, str]:
    return {
        index: normalized
        for index, cell in enumerate(row)
        if (normalized := _normalize_header(cell.value))
    }


def _row_values(row: list[_Cell], headers: dict[int, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, cell in enumerate(row):
        header = headers.get(index)
        if header and cell.value:
            values[header] = _date_like(cell.value)
    return values


def _cell_by_header(row: list[_Cell], headers: list[str], header: str) -> str | None:
    try:
        index = headers.index(header)
    except ValueError:
        return None
    if index >= len(row) or not row[index].value:
        return None
    return row[index].value


def _header_value(row: list[_Cell], index: int) -> str | None:
    if index >= len(row):
        return None
    return row[index].value or None


def _citation(
    *,
    document_id: str,
    sheet_name: str,
    cell_ref: str,
    quote_or_value: str,
) -> SourceCitation:
    return SourceCitation(
        source_type="xlsx_workbook",
        source_id=document_id,
        page_or_section=sheet_name,
        field_or_chunk_id=cell_ref,
        quote_or_value=quote_or_value,
    )


def _row_range(row: list[_Cell]) -> str:
    refs = [cell.ref for cell in row if cell.ref]
    if not refs:
        return "row"
    return f"{refs[0]}:{refs[-1]}"


def _row_quote(row: list[_Cell]) -> str:
    return " | ".join(cell.value for cell in row if cell.value)


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _date_like(value: str) -> str:
    if re.fullmatch(r"\d{5}", value):
        return (_EXCEL_EPOCH + timedelta(days=int(value))).date().isoformat()
    return value


def _infer_abnormal_flag(value: str, reference_range: str | None) -> str:
    try:
        numeric_value = float(value)
    except ValueError:
        return "unknown"
    if not reference_range:
        return "unknown"

    match = re.search(r"(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)", reference_range)
    if match is None:
        return "unknown"
    operator, threshold_text = match.groups()
    threshold = float(threshold_text)
    if operator in {"<", "<="} and numeric_value >= threshold:
        return "high"
    if operator in {">", ">="} and numeric_value <= threshold:
        return "low"
    return "normal"


def _patient_field(workbook: WorkbookExtraction, field: str) -> str | None:
    record = workbook.patient_fields.get(field)
    return record.value if record is not None else None


def _patient_identifiers(workbook: WorkbookExtraction) -> list[dict[str, str]]:
    mrn = _patient_field(workbook, "MRN")
    if not mrn:
        return []
    return [{"id": mrn, "type": "MR"}]


def _latest_collection_date(results: list[LabResult]) -> str | None:
    dates = sorted({result.collection_date for result in results if result.collection_date})
    return dates[-1] if dates else None
