/**
 * Single decision module for no-patient / patient-focused / panel-capable
 * gating across the welcome state, prompt suggestions, composer placeholder,
 * Send button, and patient-specific affordances (issue 043, story 34).
 *
 * The chat surface lives in three contexts:
 *
 * - ``no-patient``: no patient is resolved AND no panel surface is mounted.
 *   This is the EHR-launch fallback when the SMART context did not pin a
 *   patient. Patient-specific actions are disabled with a clear reason; the
 *   panel route is not advertised because the surrounding shell has no
 *   panel UI.
 * - ``panel-capable``: no patient is resolved BUT the surrounding shell
 *   exposes a panel surface (the standalone app's care-team panel). Panel-
 *   wide prompts are enabled here — the W-1 panel route runs without a
 *   selected patient — while patient-specific prompts still require a
 *   patient to resolve first.
 * - ``patient-focused``: a patient is resolved (SMART context, panel click,
 *   or backend ``state.patient_id`` from the latest /chat response). Both
 *   patient-specific and panel-wide prompts are enabled.
 *
 * Returned ``…DisabledReason`` strings are user-facing copy. The Send
 * button reason is non-null only when the input itself cannot ship a
 * request (empty draft); the placeholder explains routing limitations
 * separately.
 */

import { cleanSyntheticNameSuffixes } from './displayName';

export type AgentContextKind = 'no-patient' | 'panel-capable' | 'patient-focused';

export interface AgentContextInputs {
  /** Resolved patient id from SMART launch, panel click, or
   *  ``state.patient_id`` on the latest /chat response. Empty string =
   *  no patient yet. */
  readonly focusPatientId: string;
  /** True when the surrounding shell mounts a panel surface (the
   *  standalone app's care-team panel). The EHR-launch shell does not,
   *  so this is false there even though the W-1 route would technically
   *  succeed — without a panel surface the user can't audit it. */
  readonly hasPanelSurface: boolean;
  /** Optional user-facing patient name. Only rendered when patient is
   *  resolved; ignored otherwise. */
  readonly focusPatientName?: string;
}

/**
 * Issue 044: a contextual prompt pill rendered in the Welcome card. Pills
 * carry a separate display ``label`` and a ``promptText`` because the
 * rendered chip text ("Get brief on Robert Hayes") and the user-visible
 * prompt sent on click ("Give me a brief on Robert Hayes.") are not
 * always identical.
 */
export interface PromptPill {
  readonly id: string;
  readonly icon: string;
  /** Chip-rendered text. */
  readonly label: string;
  /** Prompt sent to /chat when the chip is clicked. The pill click is
   *  the explicit user action; this text appears in the transcript as a
   *  normal user turn (not auto-asked). */
  readonly promptText: string;
}

export interface AgentContextDecision {
  readonly kind: AgentContextKind;
  /** Composer ``<input>`` placeholder. Reflects the active context so
   *  the user sees what kinds of questions can ship. */
  readonly composerPlaceholder: string;
  /** Patient-specific welcome / suggestion prompts (chart, overnight,
   *  upload). Disabled with a reason in no-patient and panel-capable
   *  contexts. */
  readonly patientPromptsEnabled: boolean;
  readonly patientPromptDisabledReason: string | null;
  /** Patient-focused prompt pills (issue 044). Includes brief,
   *  medications, and overnight-trends pills, each scoped to the
   *  resolved patient when available. Always returned non-empty so the
   *  Welcome card can render the affordance even in no-patient /
   *  panel-capable contexts (where the chips read as disabled). The
   *  patient-focused context interpolates ``focusPatientName`` into
   *  each label / prompt; other contexts use generic copy. */
  readonly patientPromptPills: readonly PromptPill[];
  /** Panel-wide welcome / suggestion prompts (cohort triage). Enabled
   *  only when the panel surface is mounted; disabled with a reason in
   *  the EHR-launch no-patient context. */
  readonly panelPromptsEnabled: boolean;
  readonly panelPromptDisabledReason: string | null;
  /** Welcome card lead copy. Speaks in product language for every
   *  context, never implies "this patient" before one is resolved. */
  readonly welcomeHeadline: string;
  readonly welcomeSubcopy: string;
  /** Send button gating. ``null`` = enabled (subject to a non-empty
   *  draft). A non-null string is shown as the button's title /
   *  aria-disabled reason so the disabled state is understandable from
   *  visible UI. */
  readonly sendDisabledHint: string;
}

const NO_PATIENT_PLACEHOLDER =
  'Select a patient to ask about a chart…';
const PANEL_CAPABLE_PLACEHOLDER =
  'Ask about your panel, or pick a patient for chart questions…';
const PATIENT_FOCUSED_PLACEHOLDER =
  'Ask about this patient or your panel…';

const PATIENT_DISABLED_REASON =
  'Select a patient first.';
const PANEL_DISABLED_REASON =
  'Open a panel to use cohort prompts.';

/**
 * Issue 044: derive the three patient-focused pills (brief, medications,
 * overnight) from the resolved patient name. When no clinical name is
 * available we fall back to generic copy so the Welcome card can still
 * render the affordance (disabled with a reason) in no-patient and
 * panel-capable contexts.
 *
 * A name beginning with ``Patient/`` is the EHR-launch synthetic display
 * label (no server-side name resolution yet); we treat it the same as no
 * name so the prompt does not read "Give me a brief on Patient/123."
 */
export function derivePatientPromptPills(
  patientName: string | undefined,
): readonly PromptPill[] {
  const trimmed = cleanSyntheticNameSuffixes(patientName?.trim() ?? '');
  const hasClinicalName = trimmed.length > 0 && !trimmed.startsWith('Patient/');
  if (hasClinicalName) {
    return [
      {
        id: 'brief',
        icon: '📋',
        label: `Get brief on ${trimmed}`,
        promptText: `Give me a brief on ${trimmed}.`,
      },
      {
        id: 'medications',
        icon: '💊',
        label: `Get medications on ${trimmed}`,
        promptText: `What medications is ${trimmed} on?`,
      },
      {
        id: 'overnight',
        icon: '☾',
        label: `Overnight trends for ${trimmed}`,
        promptText: `What happened overnight for ${trimmed}?`,
      },
    ];
  }
  return [
    {
      id: 'brief',
      icon: '📋',
      label: 'Get brief on patient',
      promptText: 'Give me a brief on this patient.',
    },
    {
      id: 'medications',
      icon: '💊',
      label: 'Get medications on patient',
      promptText: 'What medications is this patient on?',
    },
    {
      id: 'overnight',
      icon: '☾',
      label: 'Overnight trends',
      promptText: 'What happened overnight for this patient?',
    },
  ];
}

export function deriveAgentContext(
  inputs: AgentContextInputs,
): AgentContextDecision {
  const hasPatient = inputs.focusPatientId.trim().length > 0;
  const hasPanel = inputs.hasPanelSurface;
  const patientPromptPills = derivePatientPromptPills(
    hasPatient ? inputs.focusPatientName : undefined,
  );

  if (hasPatient) {
    const name = cleanSyntheticNameSuffixes(inputs.focusPatientName?.trim() ?? '');
    const hasClinicalName = !!name && name.length > 0 && !name.startsWith('Patient/');
    const subjectPossessive = hasClinicalName ? `${name}'s` : "the patient's";
    return {
      kind: 'patient-focused',
      composerPlaceholder: PATIENT_FOCUSED_PLACEHOLDER,
      patientPromptsEnabled: true,
      patientPromptDisabledReason: null,
      patientPromptPills,
      panelPromptsEnabled: hasPanel,
      panelPromptDisabledReason: hasPanel ? null : PANEL_DISABLED_REASON,
      welcomeHeadline: 'How can I help with this chart?',
      welcomeSubcopy:
        `I read ${subjectPossessive} record over FHIR — I won't write orders or notes ` +
        'without your confirmation.',
      sendDisabledHint: 'Type a message to send.',
    };
  }

  if (hasPanel) {
    return {
      kind: 'panel-capable',
      composerPlaceholder: PANEL_CAPABLE_PLACEHOLDER,
      patientPromptsEnabled: false,
      patientPromptDisabledReason: PATIENT_DISABLED_REASON,
      patientPromptPills,
      panelPromptsEnabled: true,
      panelPromptDisabledReason: null,
      welcomeHeadline: 'How can I help today?',
      welcomeSubcopy:
        "I can summarize your panel, or pick a patient and I'll read their " +
        "record over FHIR. I won't write orders or notes without your confirmation.",
      sendDisabledHint:
        'Type a message to send. Pick a patient first for chart questions.',
    };
  }

  return {
    kind: 'no-patient',
    composerPlaceholder: NO_PATIENT_PLACEHOLDER,
    patientPromptsEnabled: false,
    patientPromptDisabledReason: PATIENT_DISABLED_REASON,
    patientPromptPills,
    panelPromptsEnabled: false,
    panelPromptDisabledReason: PANEL_DISABLED_REASON,
    welcomeHeadline: 'Select a patient to begin',
    welcomeSubcopy:
      "Pick a patient to ask about their chart. I read records over FHIR " +
      "and won't write orders or notes without your confirmation.",
    sendDisabledHint:
      'Select a patient to enable chart questions.',
  };
}
