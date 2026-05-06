"""Deterministic document-type guard (issue 024).

A no-VLM safety net that catches obvious mismatches between the clinician's
selected ``doc_type`` and the file's actual content. The guard combines:

1. Filename hints — keyword matches against the upload filename. Cheap and
   unreliable on its own (a clinician might rename a file or upload a
   scanned image with a generic name), but a useful prior.
2. PDF text cues — for PDFs, extract the first page's text via PyMuPDF
   and count distinctive phrase matches per type. Lab reports and intake
   forms have very different phrase vocabularies (``Reference Range``,
   ``Ordering Provider``, ``CLIA`` vs ``Patient Demographics``, ``Chief
   Complaint``, ``Emergency Contact``).
3. PNG/JPEG: filename is the only signal — at most ``medium`` confidence.

The guard does NOT call the VLM. It is meant to be cheap, deterministic,
and run before any extraction so the upload path can warn before the
wrong pipeline runs.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Literal

import fitz  # PyMuPDF

DocType = Literal["lab_pdf", "intake_form"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of a document-type detection.

    ``detected_type`` is ``None`` when no signal pointed at either type
    (e.g. an empty filename and unreadable bytes); callers should treat
    this as ``low`` confidence and let the user's selection stand.
    """

    detected_type: DocType | None
    confidence: Confidence
    evidence: tuple[str, ...] = field(default_factory=tuple)


# Filename keyword sets — matched as case-insensitive substrings against
# the upload filename. Kept narrow so they don't fire on unrelated words.
_LAB_FILENAME_TOKENS: tuple[str, ...] = (
    "lab",
    "labs",
    "cbc",
    "cmp",
    "lipid",
    "hba1c",
    "a1c",
    "tsh",
    "panel",
    "result",
    "results",
    "cholesterol",
    "metabolic",
    "urinalysis",
)

_INTAKE_FILENAME_TOKENS: tuple[str, ...] = (
    "intake",
    "demographics",
    "questionnaire",
    "history-form",
    "new-patient",
    "newpatient",
    "registration",
)

# Distinctive phrase cues — exact, case-insensitive matches against the
# extracted PDF text. Each match counts once even if it appears multiple
# times. The phrase vocabularies were chosen by inspecting the fixture
# corpus (``example-documents/``) for terms that show up in one type
# and not the other.
_LAB_TEXT_CUES: tuple[str, ...] = (
    "reference range",
    "ordering provider",
    "accession",
    "clia #",
    "cap #",
    "specimen",
    "report status",
    "diagnostics lab",
    "reference lab",
    "clinical laboratory",
    "laboratory services",
    "report date",
    "abnormal flag",
    "out of range",
    "ng/ml",
    "mg/dl",
    "mmol/l",
    "lipid panel",
    "complete blood count",
    "comprehensive metabolic",
    "hemoglobin a1c",
)

_INTAKE_TEXT_CUES: tuple[str, ...] = (
    "patient intake",
    "intake form",
    "new patient intake",
    "patient demographics",
    "chief complaint",
    "chief concern",
    "reason for visit",
    "front desk",
    "preferred language",
    "emergency contact",
    "primary insurance",
    "social history",
    "family history",
    "current medications",
    "known allergies",
    "marital status",
    "employer",
    "visit type",
    "preferred pharmacy",
)


def detect_doc_type(
    file_data: bytes,
    filename: str,
    mimetype: str,
) -> DetectionResult:
    """Classify ``file_data`` as a lab PDF, intake form, or unknown.

    The contract is intentionally narrow: callers compare ``detected_type``
    against the user's selected type and only act on a ``high``-confidence
    disagreement. ``low`` confidence means "we don't know — trust the user".
    """
    name_lower = filename.lower()
    fname_evidence: list[str] = []
    lab_score = 0
    intake_score = 0

    for token in _LAB_FILENAME_TOKENS:
        if token in name_lower:
            lab_score += 1
            fname_evidence.append(f"filename contains '{token}'")
            break  # one filename hint is enough

    for token in _INTAKE_FILENAME_TOKENS:
        if token in name_lower:
            intake_score += 1
            fname_evidence.append(f"filename contains '{token}'")
            break

    text_evidence: list[str] = []
    text_lab = 0
    text_intake = 0
    if _looks_like_pdf(file_data, mimetype):
        text = _extract_first_page_text(file_data)
        if text:
            text_lower = text.lower()
            for cue in _LAB_TEXT_CUES:
                if cue in text_lower:
                    text_lab += 1
                    text_evidence.append(f"text contains '{cue}'")
            for cue in _INTAKE_TEXT_CUES:
                if cue in text_lower:
                    text_intake += 1
                    text_evidence.append(f"text contains '{cue}'")

    lab_total = lab_score + text_lab
    intake_total = intake_score + text_intake

    if lab_total == 0 and intake_total == 0:
        return DetectionResult(detected_type=None, confidence="low", evidence=())

    detected: DocType
    confidence: Confidence
    if lab_total > intake_total:
        detected = "lab_pdf"
        delta = lab_total - intake_total
        confidence = _confidence_from_score(text_lab, delta)
    elif intake_total > lab_total:
        detected = "intake_form"
        delta = intake_total - lab_total
        confidence = _confidence_from_score(text_intake, delta)
    else:
        # Tied: signals contradict each other; refuse to commit.
        return DetectionResult(
            detected_type=None,
            confidence="low",
            evidence=tuple(fname_evidence + text_evidence),
        )

    return DetectionResult(
        detected_type=detected,
        confidence=confidence,
        evidence=tuple(fname_evidence + text_evidence),
    )


def _confidence_from_score(text_winner_hits: int, delta: int) -> Confidence:
    """Map text cue counts and margin onto a coarse confidence band.

    A solid PDF read (4+ distinctive phrases) earns ``high`` confidence.
    Without text cues we top out at ``medium`` — filename alone is not
    strong enough to override a clinician's selection.
    """
    if text_winner_hits >= 4 and delta >= 3:
        return "high"
    if text_winner_hits >= 2 and delta >= 1:
        return "medium"
    if text_winner_hits >= 1 or delta >= 1:
        return "medium"
    return "low"


def _looks_like_pdf(file_data: bytes, mimetype: str) -> bool:
    if mimetype == "application/pdf":
        return True
    return file_data.startswith(b"%PDF-")


def _extract_first_page_text(file_data: bytes) -> str:
    """Return the first page's text, or ``""`` on any error.

    PyMuPDF can raise on malformed PDFs; the guard treats any failure as
    "no text signal" rather than letting a malformed-but-uploadable file
    explode the upload path.
    """
    try:
        with fitz.open(stream=io.BytesIO(file_data), filetype="pdf") as doc:
            if doc.page_count == 0:
                return ""
            return doc[0].get_text() or ""
    except Exception:
        return ""
