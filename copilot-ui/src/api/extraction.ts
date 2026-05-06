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

export interface ExtractionResponse {
  readonly document_id: string;
  readonly doc_type: DocType;
  readonly filename: string;
  readonly lab: LabExtraction | null;
  readonly intake: IntakeExtraction | null;
}
