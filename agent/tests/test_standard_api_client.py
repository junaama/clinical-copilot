"""Unit tests for ``StandardApiClient`` — mocked HTTP responses.

Covers:
* Successful operations return ``(True, id, None, latency_ms)``
* HTTP error codes surface as ``http_<status>`` errors
* Transport failures surface as ``transport: <exception>`` errors
* Missing token returns ``no_token`` error
* All four methods: upload_document, create_allergy, create_medication,
  create_medical_problem
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from copilot.config import Settings
from copilot.standard_api_client import StandardApiClient


def _settings() -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        OPENEMR_BASE_URL="http://localhost:8300",
        OPENEMR_FHIR_TOKEN="test-token",
        USE_FIXTURE_FHIR=True,
    )


@pytest.fixture()
def client() -> StandardApiClient:
    return StandardApiClient(_settings())


# ---------------------------------------------------------------------------
# upload_document
# ---------------------------------------------------------------------------


async def test_upload_document_success(client: StandardApiClient) -> None:
    response = httpx.Response(
        201,
        json={"id": "42"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, doc_id, err, _ms = await client.upload_document(
            "patient-1", b"PDF content", "lab.pdf", "lab_results"
        )

    assert ok is True
    assert doc_id == "42"
    assert err is None
    assert isinstance(_ms, int)


async def test_upload_document_http_error(client: StandardApiClient) -> None:
    response = httpx.Response(
        413,
        json={"error": "too large"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, doc_id, err, _ms = await client.upload_document(
            "patient-1", b"big file", "huge.pdf"
        )

    assert ok is False
    assert doc_id is None
    assert err == "http_413"


async def test_upload_document_transport_error(client: StandardApiClient) -> None:
    with patch.object(
        client._client, "post", new_callable=AsyncMock,
        side_effect=httpx.ConnectError("connection refused"),
    ):
        ok, doc_id, err, _ms = await client.upload_document(
            "patient-1", b"data", "test.pdf"
        )

    assert ok is False
    assert doc_id is None
    assert err == "transport: ConnectError"


async def test_upload_document_no_token(client: StandardApiClient) -> None:
    with patch.object(client, "_resolve_token", return_value=""):
        ok, _doc_id, err, _ms = await client.upload_document(
            "patient-1", b"data", "test.pdf"
        )

    assert ok is False
    assert err == "no_token"


# ---------------------------------------------------------------------------
# create_allergy
# ---------------------------------------------------------------------------


async def test_create_allergy_success(client: StandardApiClient) -> None:
    response = httpx.Response(
        201,
        json={"id": "101"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, allergy_id, err, _ms = await client.create_allergy(
            "patient-1", {"title": "Penicillin"}
        )

    assert ok is True
    assert allergy_id == "101"
    assert err is None


async def test_create_allergy_unauthorized(client: StandardApiClient) -> None:
    response = httpx.Response(
        401,
        json={"error": "unauthorized"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, allergy_id, err, _ms = await client.create_allergy(
            "patient-1", {"title": "Penicillin"}
        )

    assert ok is False
    assert allergy_id is None
    assert err == "http_401"


async def test_create_allergy_no_token(client: StandardApiClient) -> None:
    with patch.object(client, "_resolve_token", return_value=""):
        ok, _allergy_id, err, _ms = await client.create_allergy(
            "patient-1", {"title": "Penicillin"}
        )

    assert ok is False
    assert err == "no_token"


# ---------------------------------------------------------------------------
# create_medication
# ---------------------------------------------------------------------------


async def test_create_medication_success(client: StandardApiClient) -> None:
    response = httpx.Response(
        201,
        json={"id": "202"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, med_id, err, _ms = await client.create_medication(
            "patient-1", {"title": "Lisinopril 10mg", "dosage": "10mg daily"}
        )

    assert ok is True
    assert med_id == "202"
    assert err is None


async def test_create_medication_not_found(client: StandardApiClient) -> None:
    response = httpx.Response(
        404,
        json={"error": "patient not found"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, med_id, err, _ms = await client.create_medication(
            "nonexistent", {"title": "Aspirin"}
        )

    assert ok is False
    assert med_id is None
    assert err == "http_404"


async def test_create_medication_transport_error(client: StandardApiClient) -> None:
    with patch.object(
        client._client, "post", new_callable=AsyncMock,
        side_effect=httpx.ReadTimeout("read timeout"),
    ):
        ok, _med_id, err, _ms = await client.create_medication(
            "patient-1", {"title": "Aspirin"}
        )

    assert ok is False
    assert err == "transport: ReadTimeout"


# ---------------------------------------------------------------------------
# create_medical_problem
# ---------------------------------------------------------------------------


async def test_create_medical_problem_success(client: StandardApiClient) -> None:
    response = httpx.Response(
        200,
        json={"id": "303"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, problem_id, err, _ms = await client.create_medical_problem(
            "patient-1", {"title": "Essential hypertension", "begdate": "2024-01-15"}
        )

    assert ok is True
    assert problem_id == "303"
    assert err is None


async def test_create_medical_problem_server_error(client: StandardApiClient) -> None:
    response = httpx.Response(
        500,
        json={"error": "internal error"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, problem_id, err, _ms = await client.create_medical_problem(
            "patient-1", {"title": "Diabetes"}
        )

    assert ok is False
    assert problem_id is None
    assert err == "http_500"


async def test_create_medical_problem_no_token(client: StandardApiClient) -> None:
    with patch.object(client, "_resolve_token", return_value=""):
        ok, _problem_id, err, _ms = await client.create_medical_problem(
            "patient-1", {"title": "Diabetes"}
        )

    assert ok is False
    assert err == "no_token"


# ---------------------------------------------------------------------------
# Base URL construction
# ---------------------------------------------------------------------------


async def test_base_url_construction(client: StandardApiClient) -> None:
    assert client._base_url == "http://localhost:8300/apis/default/api"


# ---------------------------------------------------------------------------
# FHIR-prefix stripping — guards against re-introducing the bug where
# Patient/<uuid> ended up double-nested in URLs (.../patient/Patient/<uuid>/
# allergy 404'd from OpenEMR's Standard API).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "args", "expected_suffix"),
    [
        (
            "upload_document",
            ("Patient/abc-123", b"%PDF-1.4 ...", "x.pdf"),
            "/patient/abc-123/document",
        ),
        (
            "create_allergy",
            ("Patient/abc-123", {"title": "penicillin"}),
            "/patient/abc-123/allergy",
        ),
        (
            "create_medication",
            ("Patient/abc-123", {"title": "metformin"}),
            "/patient/abc-123/medication",
        ),
        (
            "create_medical_problem",
            ("Patient/abc-123", {"title": "diabetes"}),
            "/patient/abc-123/medical_problem",
        ),
    ],
)
async def test_methods_strip_fhir_prefix_from_patient_id(
    client: StandardApiClient,
    method_name: str,
    args: tuple,
    expected_suffix: str,
) -> None:
    response = httpx.Response(
        201,
        json={"id": "1"},
        request=httpx.Request("POST", "http://test/"),
    )
    method = getattr(client, method_name)
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response) as mock_post:
        await method(*args)

    called_url = mock_post.call_args.args[0]
    assert called_url.endswith(expected_suffix), called_url
    assert "Patient/" not in called_url
