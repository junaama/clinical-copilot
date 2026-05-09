"""Lab-result persistence backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .schemas import LabExtraction


@dataclass(frozen=True)
class LabPersistenceItem:
    field_path: str
    persistence_status: str
    procedure_order_id: int | None = None
    procedure_report_id: int | None = None
    procedure_result_id: int | None = None
    observation_id: str | None = None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "field_path": self.field_path,
            "persistence_status": self.persistence_status,
        }
        if self.procedure_order_id is not None:
            body["procedure_order_id"] = self.procedure_order_id
        if self.procedure_report_id is not None:
            body["procedure_report_id"] = self.procedure_report_id
        if self.procedure_result_id is not None:
            body["procedure_result_id"] = self.procedure_result_id
        if self.observation_id is not None:
            body["observation_id"] = self.observation_id
        if self.error is not None:
            body["error"] = self.error
        return body


@dataclass(frozen=True)
class LabPersistenceResult:
    persistence_status: str
    results: tuple[LabPersistenceItem, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "persistence_status": self.persistence_status,
            "results": [item.to_dict() for item in self.results],
        }
        if self.error is not None:
            body["error"] = self.error
        return body


class LabResultPersister(Protocol):
    async def persist(
        self,
        *,
        patient_id: str,
        extracted_labs: LabExtraction,
    ) -> LabPersistenceResult:
        """Persist extracted labs and return per-result status."""


class OpenEmrLabResultPersister:
    """Persist lab rows through the custom OpenEMR module endpoint."""

    def __init__(self, standard_client: Any) -> None:
        self._standard = standard_client

    async def persist(
        self,
        *,
        patient_id: str,
        extracted_labs: LabExtraction,
    ) -> LabPersistenceResult:
        payload = {"results": _lab_results_payload(extracted_labs)}
        ok, body, error, _latency_ms = await self._standard.create_lab_result(
            patient_id,
            payload,
        )
        if not ok:
            message = error or "unknown"
            return LabPersistenceResult(
                persistence_status="failed",
                results=tuple(
                    LabPersistenceItem(
                        field_path=f"results.{index}",
                        persistence_status="failed",
                        error={
                            "code": "openemr_module_write_failed",
                            "message": message,
                        },
                    )
                    for index, _lab in enumerate(extracted_labs.results)
                ),
                error=message,
            )

        return _result_from_module_body(body or {})


def _lab_results_payload(extraction: LabExtraction) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, lab in enumerate(extraction.results):
        rows.append(
            {
                "field_path": f"results.{index}",
                "source_document_id": extraction.source_document_id,
                "loinc_code": lab.loinc_code,
                "test_name": lab.test_name,
                "value": lab.value,
                "unit": _normalize_unit(lab.unit),
                "original_unit": lab.unit,
                "reference_range": lab.reference_range,
                "effective_datetime": lab.collection_date or extraction.collection_date,
                "ordering_provider": extraction.ordering_provider,
                "abnormal_flag": lab.abnormal_flag,
            }
        )
    return rows


def _normalize_unit(unit: str) -> str:
    ucum = {
        "mg/dl": "mg/dL",
        "mg per dl": "mg/dL",
        "mmol/l": "mmol/L",
        "u/l": "U/L",
        "iu/l": "IU/L",
    }
    return ucum.get(unit.strip().lower(), unit)


def _result_from_module_body(body: dict[str, Any]) -> LabPersistenceResult:
    items: list[LabPersistenceItem] = []
    for raw in body.get("results") or []:
        if not isinstance(raw, dict):
            continue
        error = raw.get("error") if isinstance(raw.get("error"), dict) else None
        items.append(
            LabPersistenceItem(
                field_path=str(raw.get("field_path") or ""),
                persistence_status=str(raw.get("persistence_status") or "failed"),
                procedure_order_id=_optional_int(raw.get("procedure_order_id")),
                procedure_report_id=_optional_int(raw.get("procedure_report_id")),
                procedure_result_id=_optional_int(raw.get("procedure_result_id")),
                observation_id=(
                    str(raw["observation_id"]) if raw.get("observation_id") else None
                ),
                error={
                    "code": str(error.get("code") or "openemr_module_write_failed"),
                    "message": str(error.get("message") or "unknown"),
                }
                if error is not None
                else None,
            )
        )

    status = str(body.get("persistence_status") or _aggregate_status(items))
    return LabPersistenceResult(persistence_status=status, results=tuple(items))


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aggregate_status(items: list[LabPersistenceItem]) -> str:
    if not items:
        return "succeeded"
    failed = sum(1 for item in items if item.persistence_status == "failed")
    if failed == 0:
        return "succeeded"
    if failed == len(items):
        return "failed"
    return "partial"
