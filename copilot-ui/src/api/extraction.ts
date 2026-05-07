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

export interface IntakeMedication {
  readonly name: string;
  readonly dose: string | null;
  readonly frequency: string | null;
  readonly confidence: Confidence;
}

export interface IntakeAllergy {
  readonly substance: string;
  readonly reaction: string | null;
  readonly severity: string | null;
  readonly confidence: Confidence;
}

export interface IntakeDemographics {
  readonly name: string | null;
  readonly date_of_birth: string | null;
  readonly sex: string | null;
  readonly phone: string | null;
  readonly address: string | null;
}

export interface FamilyHistoryEntry {
  readonly relation: string;
  readonly condition: string;
  readonly confidence: Confidence;
}

export interface SocialHistory {
  readonly tobacco: string | null;
  readonly alcohol: string | null;
  readonly substance_use: string | null;
  readonly occupation: string | null;
}

export interface IntakeExtraction {
  readonly demographics: IntakeDemographics;
  readonly chief_concern: string | null;
  readonly current_medications: readonly IntakeMedication[];
  readonly allergies: readonly IntakeAllergy[];
  readonly family_history: readonly FamilyHistoryEntry[];
  readonly social_history: SocialHistory | null;
}

export type DocType = 'lab_pdf' | 'intake_form';

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
}
