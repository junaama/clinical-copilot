/**
 * Types for the document extraction feature (issue 011).
 *
 * These mirror the Pydantic schemas being built in issue 002 / agent-side
 * extraction module. The wire shape comes back from POST /upload; this file
 * is the TypeScript reflection used by the file-upload widget and the
 * extraction results panel.
 *
 * The shape is loose enough to absorb both lab-PDF and intake-form
 * extractions without two separate response types — `doc_type` is the
 * discriminator and the relevant payload field (`lab` or `intake`) is set.
 */

export type Confidence = 'high' | 'medium' | 'low';

export type AbnormalFlag = 'high' | 'low' | 'normal' | 'critical' | 'unknown';

export interface BoundingBox {
  readonly page: number;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface LabResult {
  readonly test_name: string;
  readonly value: string;
  readonly unit: string;
  readonly reference_range: string | null;
  readonly abnormal_flag: AbnormalFlag;
  readonly confidence: Confidence;
}

export interface LabExtraction {
  readonly patient_name: string | null;
  readonly collection_date: string | null;
  readonly lab_name: string | null;
  readonly ordering_provider: string | null;
  readonly results: readonly LabResult[];
}

/**
 * Intake-form sub-shapes mirror the backend Pydantic models in
 * ``agent/src/copilot/extraction/schemas.py`` (issue 034). Fields the
 * VLM is told to emit "as-is" are nullable strings; required fields are
 * required because the backend persistence layer cannot write them
 * blank (medication name, allergy substance, family-history relation
 * and condition).
 */
export interface IntakeMedication {
  readonly name: string;
  readonly dose: string | null;
  readonly frequency: string | null;
  readonly prescriber: string | null;
}

export interface IntakeAllergy {
  readonly substance: string;
  readonly reaction: string | null;
  readonly severity: string | null;
}

export interface IntakeDemographics {
  readonly name: string | null;
  readonly dob: string | null;
  readonly gender: string | null;
  readonly address: string | null;
  readonly phone: string | null;
  readonly emergency_contact: string | null;
}

export interface FamilyHistoryEntry {
  readonly relation: string;
  readonly condition: string;
}

export interface SocialHistory {
  readonly smoking: string | null;
  readonly alcohol: string | null;
  readonly drugs: string | null;
  readonly occupation: string | null;
}

export interface IntakeExtraction {
  readonly demographics: IntakeDemographics;
  readonly chief_concern: string;
  readonly current_medications: readonly IntakeMedication[];
  readonly allergies: readonly IntakeAllergy[];
  readonly family_history: readonly FamilyHistoryEntry[];
  readonly social_history: SocialHistory | null;
}

export type DocType =
  | 'lab_pdf'
  | 'intake_form'
  | 'hl7_oru'
  | 'hl7_adt'
  | 'xlsx_workbook'
  | 'docx_referral'
  | 'tiff_fax';

/**
 * Drawable-only bbox record (issue 031).
 *
 * The backend filters records without geometry at the response boundary,
 * so every entry carries a non-null ``bbox``. The source-overlay can
 * render every record without branching on null geometry. Image uploads
 * and PDFs whose values the matcher couldn't locate produce an empty
 * array; the panel simply renders no overlay.
 */
export interface UploadBboxRecord {
  readonly field_path: string;
  readonly extracted_value: string;
  readonly matched_text: string;
  readonly bbox: BoundingBox;
  readonly match_confidence: number;
  /** Coordinate source: 'vlm' for VLM-native or 'pymupdf' for word-geometry. */
  readonly bbox_source?: 'vlm' | 'pymupdf' | null;
}

/**
 * Canonical upload-outcome status (issue 025).
 *
 * `'ok'` is the only status where the panel may render lab/intake content
 * and the app may inject a synthetic post-upload chat turn. Every other
 * value carries a user-safe ``failure_reason``.
 */
export type UploadStatus =
  | 'ok'
  | 'upload_failed'
  | 'doc_ref_failed'
  | 'extraction_failed'
  | 'persistence_failed'
  | 'unauthorized';

export interface ExtractionResponse {
  readonly status: UploadStatus;
  readonly requested_type: DocType;
  /** Effective doc type (what was actually extracted). null on failure. */
  readonly effective_type: DocType | null;
  readonly document_id: string | null;
  /** Canonical "DocumentReference/<id>" form. null when no real id exists. */
  readonly document_reference: string | null;
  /**
   * Legacy mirror of ``effective_type`` for callers that haven't switched
   * to the canonical envelope yet. Same value as ``effective_type`` on
   * success; on failure this falls back to ``requested_type`` so existing
   * UI code reading ``doc_type`` keeps working.
   */
  readonly doc_type: DocType;
  readonly filename: string;
  /** True when the agent can be invited to discuss this upload. */
  readonly discussable: boolean;
  readonly lab: LabExtraction | null;
  readonly intake: IntakeExtraction | null;
  /** User-safe message — never raw exception text. null on success. */
  readonly failure_reason: string | null;
  /**
   * Drawable bbox records for the source-overlay (issue 031). Each entry
   * has non-null geometry; records the matcher couldn't locate are
   * filtered server-side. Empty array on failure or when no matches were
   * found (image uploads always produce an empty array — only PDFs have
   * extractable text geometry).
   */
  readonly bboxes: readonly UploadBboxRecord[];
}
