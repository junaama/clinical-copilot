"""Unit tests for ``DocumentClient`` — mocked HTTP responses.

Covers:
* Magic-byte validation rejects non-PDF/PNG/JPEG before HTTP call
* Size limit (20 MB) rejects oversized files before HTTP call
* upload/list/download success paths return the correct tuple shape
* HTTP error codes surface as ``http_<status>`` errors
* Transport failures surface as ``transport: <exception>`` errors
* Missing token returns ``no_token`` error
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from copilot.config import Settings
from copilot.extraction.document_client import (
    MAX_DOCUMENT_BYTES,
    DocumentClient,
)


def _settings() -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        OPENEMR_BASE_URL="http://localhost:8300",
        OPENEMR_FHIR_TOKEN="test-token",
        USE_FIXTURE_FHIR=True,
    )


@pytest.fixture()
def client() -> DocumentClient:
    return DocumentClient(_settings())


# Minimal valid magic-byte prefixes followed by filler bytes
PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"x" * 100
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"x" * 100
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"x" * 100
TXT_BYTES = b"hello, this is plain text"


# ---------------------------------------------------------------------------
# Magic-byte validation
# ---------------------------------------------------------------------------


async def test_upload_rejects_non_pdf_png_jpeg(client: DocumentClient) -> None:
    ok, doc_id, err, _ms = await client.upload(
        "patient-1", TXT_BYTES, "notes.txt"
    )

    assert ok is False
    assert doc_id is None
    assert err == "invalid_file_type"


async def test_upload_accepts_pdf(client: DocumentClient) -> None:
    response = httpx.Response(
        201,
        json={"id": "doc-pdf"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, doc_id, err, _ms = await client.upload(
            "patient-1", PDF_BYTES, "lab.pdf"
        )

    assert ok is True
    assert doc_id == "doc-pdf"
    assert err is None


async def test_upload_accepts_png(client: DocumentClient) -> None:
    response = httpx.Response(
        201,
        json={"id": "doc-png"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, doc_id, _err, _ms = await client.upload(
            "patient-1", PNG_BYTES, "scan.png"
        )

    assert ok is True
    assert doc_id == "doc-png"


async def test_upload_accepts_jpeg(client: DocumentClient) -> None:
    response = httpx.Response(
        201,
        json={"id": "doc-jpg"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, doc_id, _err, _ms = await client.upload(
            "patient-1", JPEG_BYTES, "intake.jpg"
        )

    assert ok is True
    assert doc_id == "doc-jpg"


# ---------------------------------------------------------------------------
# Size validation
# ---------------------------------------------------------------------------


async def test_upload_rejects_oversized(client: DocumentClient) -> None:
    huge = PDF_BYTES + b"\x00" * (MAX_DOCUMENT_BYTES + 1)
    ok, doc_id, err, _ms = await client.upload(
        "patient-1", huge, "huge.pdf"
    )

    assert ok is False
    assert doc_id is None
    assert err == "file_too_large"


async def test_upload_accepts_at_size_limit(client: DocumentClient) -> None:
    # File of exactly MAX_DOCUMENT_BYTES bytes that still starts with %PDF-
    # so magic-byte check passes.
    head = b"%PDF-1.4\n"
    payload = head + b"\x00" * (MAX_DOCUMENT_BYTES - len(head))
    assert len(payload) == MAX_DOCUMENT_BYTES

    response = httpx.Response(
        201,
        json={"id": "doc-edge"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, _doc_id, err, _ms = await client.upload(
            "patient-1", payload, "edge.pdf"
        )

    assert ok is True
    assert err is None


# ---------------------------------------------------------------------------
# upload — auth / transport / server errors
# ---------------------------------------------------------------------------


async def test_upload_no_token(client: DocumentClient) -> None:
    with patch.object(client, "_resolve_token", return_value=""):
        ok, doc_id, err, _ms = await client.upload(
            "patient-1", PDF_BYTES, "lab.pdf"
        )

    assert ok is False
    assert doc_id is None
    assert err == "no_token"


async def test_upload_unauthorized(client: DocumentClient) -> None:
    response = httpx.Response(
        401,
        json={"error": "unauthorized"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, _doc_id, err, _ms = await client.upload(
            "patient-1", PDF_BYTES, "lab.pdf"
        )

    assert ok is False
    assert err == "http_401"


async def test_upload_patient_not_found(client: DocumentClient) -> None:
    response = httpx.Response(
        404,
        json={"error": "not found"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, _doc_id, err, _ms = await client.upload(
            "ghost", PDF_BYTES, "lab.pdf"
        )

    assert ok is False
    assert err == "http_404"


async def test_upload_server_too_large(client: DocumentClient) -> None:
    # Server-side 413 path (e.g. proxy enforces a smaller limit). Client-side
    # check has already passed.
    response = httpx.Response(
        413,
        json={"error": "request entity too large"},
        request=httpx.Request("POST", "http://test/"),
    )
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=response):
        ok, _doc_id, err, _ms = await client.upload(
            "patient-1", PDF_BYTES, "lab.pdf"
        )

    assert ok is False
    assert err == "http_413"


async def test_upload_transport_error(client: DocumentClient) -> None:
    with patch.object(
        client._client, "post", new_callable=AsyncMock,
        side_effect=httpx.ConnectError("connection refused"),
    ):
        ok, _doc_id, err, _ms = await client.upload(
            "patient-1", PDF_BYTES, "lab.pdf"
        )

    assert ok is False
    assert err == "transport: ConnectError"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_success(client: DocumentClient) -> None:
    body = [
        {"id": "10", "name": "lab.pdf", "category": "lab_results"},
        {"id": "11", "name": "intake.png", "category": "intake"},
    ]
    response = httpx.Response(
        200,
        json=body,
        request=httpx.Request("GET", "http://test/"),
    )
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=response):
        ok, docs, err, _ms = await client.list("patient-1")

    assert ok is True
    assert err is None
    assert docs == body


async def test_list_with_category_filter(client: DocumentClient) -> None:
    response = httpx.Response(
        200,
        json=[],
        request=httpx.Request("GET", "http://test/"),
    )
    mock_get = AsyncMock(return_value=response)
    with patch.object(client._client, "get", mock_get):
        ok, _docs, _err, _ms = await client.list("patient-1", category="lab_results")

    assert ok is True
    # category is appended as a query param
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs.get("params") == {"category": "lab_results"}


async def test_list_unauthorized(client: DocumentClient) -> None:
    response = httpx.Response(
        401,
        json={"error": "unauthorized"},
        request=httpx.Request("GET", "http://test/"),
    )
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=response):
        ok, docs, err, _ms = await client.list("patient-1")

    assert ok is False
    assert docs == []
    assert err == "http_401"


async def test_list_no_token(client: DocumentClient) -> None:
    with patch.object(client, "_resolve_token", return_value=""):
        ok, docs, err, _ms = await client.list("patient-1")

    assert ok is False
    assert docs == []
    assert err == "no_token"


async def test_list_transport_error(client: DocumentClient) -> None:
    with patch.object(
        client._client, "get", new_callable=AsyncMock,
        side_effect=httpx.ReadTimeout("read timeout"),
    ):
        ok, _docs, err, _ms = await client.list("patient-1")

    assert ok is False
    assert err == "transport: ReadTimeout"


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


async def test_download_success(client: DocumentClient) -> None:
    response = httpx.Response(
        200,
        content=PDF_BYTES,
        headers={"content-type": "application/pdf"},
        request=httpx.Request("GET", "http://test/"),
    )
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=response):
        ok, data, mimetype, err, _ms = await client.download(
            "patient-1", "doc-42"
        )

    assert ok is True
    assert data == PDF_BYTES
    assert mimetype == "application/pdf"
    assert err is None


async def test_download_strips_charset_from_mimetype(client: DocumentClient) -> None:
    response = httpx.Response(
        200,
        content=b"data",
        headers={"content-type": "image/png; charset=binary"},
        request=httpx.Request("GET", "http://test/"),
    )
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=response):
        ok, _data, mimetype, _err, _ms = await client.download(
            "patient-1", "doc-1"
        )

    assert ok is True
    assert mimetype == "image/png"


async def test_download_not_found(client: DocumentClient) -> None:
    response = httpx.Response(
        404,
        content=b"",
        request=httpx.Request("GET", "http://test/"),
    )
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=response):
        ok, data, mimetype, err, _ms = await client.download(
            "patient-1", "ghost"
        )

    assert ok is False
    assert data is None
    assert mimetype is None
    assert err == "http_404"


async def test_download_no_token(client: DocumentClient) -> None:
    with patch.object(client, "_resolve_token", return_value=""):
        ok, data, mimetype, err, _ms = await client.download(
            "patient-1", "doc-1"
        )

    assert ok is False
    assert data is None
    assert mimetype is None
    assert err == "no_token"


async def test_download_transport_error(client: DocumentClient) -> None:
    with patch.object(
        client._client, "get", new_callable=AsyncMock,
        side_effect=httpx.ConnectError("connection refused"),
    ):
        ok, _data, _mt, err, _ms = await client.download(
            "patient-1", "doc-1"
        )

    assert ok is False
    assert err == "transport: ConnectError"


# ---------------------------------------------------------------------------
# Base URL construction
# ---------------------------------------------------------------------------


async def test_base_url_construction(client: DocumentClient) -> None:
    assert client._base_url == "http://localhost:8300/apis/default/api"
