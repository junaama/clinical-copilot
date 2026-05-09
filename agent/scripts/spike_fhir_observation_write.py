"""Spike OpenEMR FHIR Observation writes from an extracted lab document."""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from copilot.config import Settings
from copilot.extraction.schemas import LabExtraction, LabResult
from copilot.extraction.vlm import extract_lab
from copilot.llm import build_vision_model

LAB_RESULTS_DIR = Path("example-documents/lab-results")
TOKEN_CACHE = Path("agent/scripts/seed/secrets/last_token.json")
HTTP_TIMEOUT = httpx.Timeout(120.0, connect=15.0)

OBSERVATION_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
LOCAL_LAB_CODE_SYSTEM = "https://openemr.local/copilot/lab-test-name"
UCUM_SYSTEM = "http://unitsofmeasure.org"


@dataclass(frozen=True)
class SpikeConfig:
    """Runtime inputs for the live Observation write spike."""

    fhir_base: str
    token: str
    patient_id: str
    document_path: Path
    settings: Settings


@dataclass(frozen=True)
class AssertionResult:
    """One named spike assertion with enough context to choose issue 054/055."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ObservationAttempt:
    """POST/read/search result for one extracted lab row."""

    lab_name: str
    observation_id: str | None = None
    assertions: list[AssertionResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(assertion.passed for assertion in self.assertions)


@dataclass(frozen=True)
class SpikeReport:
    """Complete spike result."""

    document_path: Path
    patient_id: str
    extracted_count: int
    attempts: list[ObservationAttempt]
    blocker: str | None = None

    @property
    def all_passed(self) -> bool:
        return self.blocker is None and bool(self.attempts) and all(
            attempt.all_passed for attempt in self.attempts
        )

    def to_text(self) -> str:
        lines = [
            f"FHIR Observation POST spike document={self.document_path}",
            f"patient_id={self.patient_id}",
            f"extracted_labs={self.extracted_count}",
        ]
        if self.blocker:
            lines.append(f"BLOCKER: {self.blocker}")
        for index, attempt in enumerate(self.attempts, start=1):
            obs = attempt.observation_id or "<none>"
            lines.append(f"{index}. {attempt.lab_name} observation_id={obs}")
            for assertion in attempt.assertions:
                status = "PASS" if assertion.passed else "FAIL"
                lines.append(f"   [{status}] {assertion.name}: {assertion.detail}")
        if not self.attempts and not self.blocker:
            lines.append("FAIL: extraction produced no lab results")
        lines.append(f"NEXT_SLICE={'054' if self.all_passed else '055'}")
        return "\n".join(lines)


def repo_root_from_here() -> Path:
    """Return the repository root from this script location."""

    return Path(__file__).resolve().parents[2]


def missing_live_inputs(repo_root: Path | None = None) -> list[str]:
    """List missing live inputs without exposing secret values."""

    root = repo_root or repo_root_from_here()
    settings = _load_settings(root)
    missing: list[str] = []
    if not settings.anthropic_api_key.get_secret_value():
        missing.append("ANTHROPIC_API_KEY")
    if not _resolve_token(root, settings):
        missing.append("OPENEMR_FHIR_TOKEN")
    if not _resolve_patient_id():
        missing.append("FHIR_OBSERVATION_SPIKE_PATIENT_ID or E2E_PATIENT_UUID")
    if not _find_lab_document(root):
        missing.append(str(root / LAB_RESULTS_DIR))
    return missing


def load_config(repo_root: Path | None = None, document: str | None = None) -> SpikeConfig:
    """Load config from env and checked-in fixture paths."""

    root = repo_root or repo_root_from_here()
    settings = _load_settings(root)
    document_path = Path(document) if document else (_find_lab_document(root) or Path())
    if not document_path.is_absolute():
        document_path = root / document_path
    if not document_path.exists():
        raise RuntimeError(f"lab fixture missing: {document_path}")
    if not settings.anthropic_api_key.get_secret_value():
        raise RuntimeError("missing VLM credential: set ANTHROPIC_API_KEY")

    token = _resolve_token(root, settings)
    if not token:
        raise RuntimeError(
            "missing write-capable SMART token: set OPENEMR_FHIR_TOKEN "
            "or create agent/scripts/seed/secrets/last_token.json"
        )

    patient_id = _resolve_patient_id()
    if not patient_id:
        raise RuntimeError(
            "missing patient id: set FHIR_OBSERVATION_SPIKE_PATIENT_ID "
            "or E2E_PATIENT_UUID"
        )

    return SpikeConfig(
        fhir_base=settings.openemr_fhir_base.rstrip("/"),
        token=token,
        patient_id=strip_fhir_prefix(patient_id, "Patient"),
        document_path=document_path,
        settings=settings,
    )


async def run_spike(
    *,
    repo_root: Path | None = None,
    document: str | None = None,
) -> SpikeReport:
    """Run the live spike and return a structured report."""

    try:
        config = load_config(repo_root, document)
    except RuntimeError as exc:
        root = repo_root or repo_root_from_here()
        doc = Path(document) if document else (_find_lab_document(root) or root / LAB_RESULTS_DIR)
        return SpikeReport(
            document_path=doc,
            patient_id=_resolve_patient_id() or "<missing>",
            extracted_count=0,
            attempts=[],
            blocker=str(exc),
        )

    try:
        extraction = await extract_labs(config.document_path, settings=config.settings)
    except RuntimeError as exc:
        return SpikeReport(
            document_path=config.document_path,
            patient_id=config.patient_id,
            extracted_count=0,
            attempts=[],
            blocker=str(exc),
        )
    if not extraction.results:
        return SpikeReport(
            document_path=config.document_path,
            patient_id=config.patient_id,
            extracted_count=0,
            attempts=[],
            blocker="extraction produced zero lab rows",
        )

    headers = {
        "Accept": "application/fhir+json",
        "Content-Type": "application/fhir+json",
        "Authorization": f"Bearer {config.token}",
    }
    attempts: list[ObservationAttempt] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
        for lab in extraction.results:
            attempt = await _post_and_assert(
                client,
                fhir_base=config.fhir_base,
                patient_id=config.patient_id,
                lab=lab,
                extraction_collection_date=extraction.collection_date,
            )
            attempts.append(attempt)

    return SpikeReport(
        document_path=config.document_path,
        patient_id=config.patient_id,
        extracted_count=len(extraction.results),
        attempts=attempts,
    )


async def extract_labs(
    document_path: Path,
    *,
    settings: Settings | None = None,
) -> LabExtraction:
    """Run the existing VLM lab extraction pipeline on a real fixture file."""

    settings = settings or _load_settings(repo_root_from_here())
    model = build_vision_model(settings)
    file_data = document_path.read_bytes()
    mimetype = _guess_mimetype(document_path)
    result = await extract_lab(
        file_data,
        mimetype,
        document_id=f"DocumentReference/local-{document_path.stem}",
        model=model,
        extraction_model_name=settings.vlm_model,
    )
    if not result.ok or not isinstance(result.extraction, LabExtraction):
        raise RuntimeError(result.error or "lab extraction failed")
    return result.extraction


def observation_payload_from_lab(
    lab: LabResult,
    *,
    patient_id: str,
    extraction_collection_date: str | None,
) -> dict[str, Any]:
    """Map one extracted lab row to a minimal FHIR Observation resource."""

    quantity_value = _parse_numeric_quantity(lab.value)
    effective = lab.collection_date or extraction_collection_date
    payload: dict[str, Any] = {
        "resourceType": "Observation",
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": OBSERVATION_CATEGORY_SYSTEM,
                        "code": "laboratory",
                        "display": "Laboratory",
                    }
                ],
                "text": "Laboratory",
            }
        ],
        "code": {
            "coding": [
                {
                    "system": LOCAL_LAB_CODE_SYSTEM,
                    "code": _local_code(lab.test_name),
                    "display": lab.test_name,
                }
            ],
            "text": lab.test_name,
        },
        "subject": {"reference": f"Patient/{strip_fhir_prefix(patient_id, 'Patient')}"},
        "valueQuantity": {
            "value": quantity_value,
            "unit": lab.unit,
            "system": UCUM_SYSTEM,
            "code": lab.unit,
        },
    }
    if effective:
        payload["effectiveDateTime"] = effective
    if lab.reference_range:
        payload["referenceRange"] = [{"text": lab.reference_range}]
    return payload


async def _post_and_assert(
    client: httpx.AsyncClient,
    *,
    fhir_base: str,
    patient_id: str,
    lab: LabResult,
    extraction_collection_date: str | None,
) -> ObservationAttempt:
    assertions: list[AssertionResult] = []
    observation_id: str | None = None
    expected_subject = f"Patient/{strip_fhir_prefix(patient_id, 'Patient')}"

    try:
        payload = observation_payload_from_lab(
            lab,
            patient_id=patient_id,
            extraction_collection_date=extraction_collection_date,
        )
    except ValueError as exc:
        assertions.append(AssertionResult("build valueQuantity", False, str(exc)))
        return ObservationAttempt(lab_name=lab.test_name, assertions=assertions)

    post_response = await client.post(f"{fhir_base}/Observation", json=payload)
    assertions.append(
        AssertionResult(
            "POST returns HTTP 201",
            post_response.status_code == 201,
            f"status={post_response.status_code} body={_body_excerpt(post_response)}",
        )
    )
    body = _json_or_empty(post_response)
    observation_id = _observation_id_from_response(post_response, body)
    assertions.append(
        AssertionResult(
            "POST returns non-empty Location/body id",
            bool(observation_id),
            (
                f"location={post_response.headers.get('Location', '')!r} "
                f"body_id={body.get('id', '')!r}"
            ),
        )
    )
    if not observation_id:
        return ObservationAttempt(lab_name=lab.test_name, assertions=assertions)

    read_response = await client.get(f"{fhir_base}/Observation/{observation_id}")
    read_body = _json_or_empty(read_response)
    assertions.append(
        AssertionResult(
            "GET by id matches valueQuantity and subject",
            read_response.status_code == 200
            and _subject(read_body) == expected_subject
            and _value_quantity_matches(read_body.get("valueQuantity"), payload["valueQuantity"]),
            (
                f"status={read_response.status_code} "
                f"subject={_subject(read_body)!r} expected_subject={expected_subject!r} "
                f"valueQuantity={read_body.get('valueQuantity')!r} "
                f"expected={payload['valueQuantity']!r}"
            ),
        )
    )

    search_response = await client.get(
        f"{fhir_base}/Observation",
        params={"patient": patient_id, "category": "laboratory"},
    )
    search_body = _json_or_empty(search_response)
    assertions.append(
        AssertionResult(
            "search by patient/category includes new resource",
            search_response.status_code == 200
            and _bundle_contains(search_body, observation_id),
            (
                f"status={search_response.status_code} "
                f"bundle_type={search_body.get('resourceType')!r} "
                f"entry_count={len(search_body.get('entry') or [])}"
            ),
        )
    )

    return ObservationAttempt(
        lab_name=lab.test_name,
        observation_id=observation_id,
        assertions=assertions,
    )


def strip_fhir_prefix(value: str, resource_type: str) -> str:
    prefix = f"{resource_type}/"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _load_settings(repo_root: Path) -> Settings:
    env_file = repo_root / "agent" / ".env"
    return Settings(_env_file=env_file)


def _resolve_patient_id() -> str:
    return (
        os.environ.get("FHIR_OBSERVATION_SPIKE_PATIENT_ID")
        or os.environ.get("E2E_PATIENT_UUID")
        or os.environ.get("E2E_LIVE_HTTP_PATIENT_UUID")
        or os.environ.get("SMART_PATIENT_ID")
        or ""
    ).strip()


def _resolve_token(repo_root: Path, settings: Settings | None = None) -> str:
    env_token = os.environ.get("OPENEMR_FHIR_TOKEN", "").strip()
    if env_token:
        return env_token
    if settings is not None:
        settings_token = settings.openemr_fhir_token.get_secret_value().strip()
        if settings_token:
            return settings_token
    token_path = repo_root / TOKEN_CACHE
    if not token_path.exists():
        return ""
    try:
        data = json.loads(token_path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    token = data.get("access_token")
    return token.strip() if isinstance(token, str) else ""


def _find_lab_document(repo_root: Path) -> Path | None:
    lab_dir = repo_root / LAB_RESULTS_DIR
    if not lab_dir.is_dir():
        return None
    candidates = sorted(
        path
        for path in lab_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}
    )
    return candidates[0] if candidates else None


def _guess_mimetype(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    if path.suffix.lower() == ".png":
        return "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    raise RuntimeError(f"unsupported lab fixture type: {path}")


def _parse_numeric_quantity(value: str) -> int | float:
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        raise ValueError(f"cannot map non-numeric lab value to valueQuantity: {value!r}")
    raw = match.group(0)
    parsed = float(raw)
    return int(parsed) if parsed.is_integer() else parsed


def _local_code(test_name: str) -> str:
    code = re.sub(r"[^a-z0-9]+", "-", test_name.lower()).strip("-")
    return code or "lab-result"


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _body_excerpt(response: httpx.Response, limit: int = 500) -> str:
    text = response.text.replace("\n", " ").strip()
    return text[:limit]


def _observation_id_from_response(
    response: httpx.Response,
    body: dict[str, Any],
) -> str | None:
    body_id = body.get("id")
    if isinstance(body_id, str) and body_id.strip():
        return body_id.strip()
    location = response.headers.get("Location") or response.headers.get("Content-Location")
    if not location:
        return None
    return location.rstrip("/").rsplit("/", 1)[-1] or None


def _subject(resource: dict[str, Any]) -> str | None:
    subject = resource.get("subject")
    if not isinstance(subject, dict):
        return None
    reference = subject.get("reference")
    return reference if isinstance(reference, str) else None


def _value_quantity_matches(actual: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(actual, dict):
        return False
    try:
        actual_value = float(actual["value"])
        expected_value = float(expected["value"])
    except (KeyError, TypeError, ValueError):
        return False
    actual_unit = actual.get("unit") or actual.get("code")
    expected_unit = expected.get("unit") or expected.get("code")
    return actual_value == expected_value and actual_unit == expected_unit


def _bundle_contains(bundle: dict[str, Any], observation_id: str) -> bool:
    for entry in bundle.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if isinstance(resource, dict) and resource.get("id") == observation_id:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document",
        help=(
            "Optional path to a real lab fixture. Defaults to the first file in "
            "example-documents/lab-results/."
        ),
    )
    args = parser.parse_args(argv)

    report = asyncio.run(run_spike(document=args.document))
    print(report.to_text())
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
