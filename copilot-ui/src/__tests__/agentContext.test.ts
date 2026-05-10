import { describe, expect, it } from 'vitest';
import { deriveAgentContext, derivePatientPromptPills } from '../lib/agentContext';

describe('deriveAgentContext (issue 043)', () => {
  describe('no-patient (no patient id, no panel surface — EHR-launch fallback)', () => {
    const ctx = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: false,
    });

    it('reports kind=no-patient', () => {
      expect(ctx.kind).toBe('no-patient');
    });

    it('does not say or imply "this patient" in welcome copy (AC1)', () => {
      expect(ctx.welcomeHeadline).not.toMatch(/this patient/i);
      expect(ctx.welcomeSubcopy).not.toMatch(/this patient/i);
      // The default DEFAULT_PATIENT_NAME ("this patient") and possessive
      // ("this patient's") must both be absent — pre-fix copy was
      // "I read this patient's record over FHIR".
      expect(ctx.welcomeSubcopy).not.toMatch(/this patient's/i);
    });

    it('disables patient-specific prompts with a clear reason (AC2)', () => {
      expect(ctx.patientPromptsEnabled).toBe(false);
      expect(ctx.patientPromptDisabledReason).not.toBeNull();
      expect(ctx.patientPromptDisabledReason).toMatch(/select a patient/i);
    });

    it('disables panel prompts when no panel surface is mounted (AC3)', () => {
      expect(ctx.panelPromptsEnabled).toBe(false);
      expect(ctx.panelPromptDisabledReason).not.toBeNull();
    });

    it('uses a no-patient composer placeholder (AC4)', () => {
      expect(ctx.composerPlaceholder).toMatch(/select a patient/i);
      expect(ctx.composerPlaceholder).not.toMatch(/this patient/i);
    });

    it('exposes a Send button hint that explains the disabled state (AC5)', () => {
      expect(ctx.sendDisabledHint).toMatch(/select a patient/i);
    });
  });

  describe('panel-capable (no patient id, panel surface mounted — standalone)', () => {
    const ctx = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: true,
    });

    it('reports kind=panel-capable', () => {
      expect(ctx.kind).toBe('panel-capable');
    });

    it('avoids "this patient" copy in welcome (AC1)', () => {
      expect(ctx.welcomeHeadline).not.toMatch(/this patient/i);
      expect(ctx.welcomeSubcopy).not.toMatch(/this patient/i);
    });

    it('disables patient-specific prompts with a reason (AC2)', () => {
      expect(ctx.patientPromptsEnabled).toBe(false);
      expect(ctx.patientPromptDisabledReason).toMatch(/select a patient/i);
    });

    it('enables panel prompts because the panel route runs without a patient (AC3)', () => {
      expect(ctx.panelPromptsEnabled).toBe(true);
      expect(ctx.panelPromptDisabledReason).toBeNull();
    });

    it('uses a panel-capable composer placeholder (AC4)', () => {
      expect(ctx.composerPlaceholder).toMatch(/your panel/i);
      expect(ctx.composerPlaceholder).toMatch(/pick a patient/i);
      expect(ctx.composerPlaceholder).not.toMatch(/this patient/i);
    });

    it('Send button hint mentions both that input is required and the patient gating (AC5)', () => {
      expect(ctx.sendDisabledHint.toLowerCase()).toMatch(/type/);
    });
  });

  describe('patient-focused (patient resolved — full chart access)', () => {
    const ctx = deriveAgentContext({
      focusPatientId: 'pat-1',
      focusPatientName: 'Eduardo Perez',
      hasPanelSurface: true,
    });

    it('reports kind=patient-focused', () => {
      expect(ctx.kind).toBe('patient-focused');
    });

    it('uses the resolved patient name in welcome copy', () => {
      expect(ctx.welcomeSubcopy).toMatch(/Eduardo Perez's record/);
    });

    it('enables both patient-specific and panel prompts', () => {
      expect(ctx.patientPromptsEnabled).toBe(true);
      expect(ctx.patientPromptDisabledReason).toBeNull();
      expect(ctx.panelPromptsEnabled).toBe(true);
      expect(ctx.panelPromptDisabledReason).toBeNull();
    });

    it('uses the patient-focused composer placeholder (AC4)', () => {
      expect(ctx.composerPlaceholder).toMatch(/this patient or your panel/i);
    });
  });

  describe('patient-focused with no panel surface (EHR-launch with patient)', () => {
    const ctx = deriveAgentContext({
      focusPatientId: 'pat-1',
      focusPatientName: 'Eduardo Perez',
      hasPanelSurface: false,
    });

    it('still reports kind=patient-focused', () => {
      expect(ctx.kind).toBe('patient-focused');
    });

    it('disables panel prompts when no panel surface (AC3)', () => {
      expect(ctx.panelPromptsEnabled).toBe(false);
      expect(ctx.panelPromptDisabledReason).not.toBeNull();
    });

    it('still enables patient-specific prompts', () => {
      expect(ctx.patientPromptsEnabled).toBe(true);
    });
  });

  describe('whitespace-only patient id is treated as no-patient', () => {
    const ctx = deriveAgentContext({
      focusPatientId: '   ',
      hasPanelSurface: true,
    });

    it('does not promote a whitespace id to patient-focused', () => {
      expect(ctx.kind).toBe('panel-capable');
      expect(ctx.patientPromptsEnabled).toBe(false);
    });
  });

  describe('patient-focused without a name falls back to neutral phrasing', () => {
    const ctx = deriveAgentContext({
      focusPatientId: 'pat-1',
      hasPanelSurface: true,
    });

    it('uses neutral possessive when name is unknown', () => {
      // Until the chat returns a display name, the welcome subcopy must
      // not interpolate an empty string ("I read 's record"); falls back
      // to "the patient's record".
      expect(ctx.welcomeSubcopy).toMatch(/the patient's record/);
      expect(ctx.welcomeSubcopy).not.toMatch(/^I read 's record/);
    });
  });

  describe('patient-focused with synthetic Patient/<id> name (EHR-launch fallback)', () => {
    const ctx = deriveAgentContext({
      focusPatientId: 'pat-9',
      focusPatientName: 'Patient/pat-9',
      hasPanelSurface: false,
    });

    it('does not interpolate Patient/<id> as a clinical name in welcome copy', () => {
      // The synthetic ``Patient/<id>`` label is not a clinician-facing name;
      // the subcopy should fall back to neutral possessive rather than
      // reading "I read Patient/pat-9's record".
      expect(ctx.welcomeSubcopy).toMatch(/the patient's record/);
      expect(ctx.welcomeSubcopy).not.toMatch(/Patient\/pat-9/);
    });
  });
});

describe('derivePatientPromptPills (issue 044)', () => {
  describe('with a clinical patient name', () => {
    const pills = derivePatientPromptPills('Robert Hayes');

    it('returns three pills (brief, medications, overnight)', () => {
      expect(pills).toHaveLength(3);
      const ids = pills.map((p) => p.id);
      expect(ids).toEqual(['brief', 'medications', 'overnight']);
    });

    it('interpolates the patient name into each pill label', () => {
      expect(pills[0]?.label).toBe('Get brief on Robert Hayes');
      expect(pills[1]?.label).toBe('Get medications on Robert Hayes');
      expect(pills[2]?.label).toBe('Overnight trends for Robert Hayes');
    });

    it('interpolates the patient name into each pill promptText', () => {
      expect(pills[0]?.promptText).toBe('Give me a brief on Robert Hayes.');
      expect(pills[1]?.promptText).toBe(
        'What medications is Robert Hayes on?',
      );
      expect(pills[2]?.promptText).toBe(
        'What happened overnight for Robert Hayes?',
      );
    });

    it('cleans synthetic numeric suffixes from patient names', () => {
      const pills = derivePatientPromptPills('Patricia625 Raquel318 Covarrubias273');

      expect(pills[0]?.label).toBe('Get brief on Patricia Raquel Covarrubias');
      expect(pills[0]?.promptText).toBe(
        'Give me a brief on Patricia Raquel Covarrubias.',
      );
    });
  });

  describe('without a patient name (or empty string)', () => {
    it('falls back to generic labels and prompt texts', () => {
      const pills = derivePatientPromptPills(undefined);
      expect(pills.map((p) => p.label)).toEqual([
        'Get brief on patient',
        'Get medications on patient',
        'Overnight trends',
      ]);
      // Prompt text stays meaningful — the agent receives a clear user-
      // visible question even when the name hasn't resolved yet.
      expect(pills[0]?.promptText).toBe('Give me a brief on this patient.');
      expect(pills[1]?.promptText).toBe(
        'What medications is this patient on?',
      );
      expect(pills[2]?.promptText).toBe(
        'What happened overnight for this patient?',
      );
    });

    it('treats empty string the same as undefined', () => {
      const pills = derivePatientPromptPills('');
      expect(pills[0]?.label).toBe('Get brief on patient');
    });

    it('treats whitespace-only as no name', () => {
      const pills = derivePatientPromptPills('   ');
      expect(pills[0]?.label).toBe('Get brief on patient');
    });
  });

  describe('synthetic Patient/<id> name', () => {
    it('does not interpolate the synthetic id into pill copy', () => {
      // The EHR-launch shell pre-resolution uses ``Patient/<id>`` as a
      // display label. Pills must not surface that into clinician-facing
      // prompt text — the conversation title would otherwise read
      // "Give me a brief on Patient/pat-9.".
      const pills = derivePatientPromptPills('Patient/pat-9');
      pills.forEach((p) => {
        expect(p.label).not.toMatch(/Patient\/pat-9/);
        expect(p.promptText).not.toMatch(/Patient\/pat-9/);
      });
      expect(pills[0]?.label).toBe('Get brief on patient');
    });
  });

  describe('agent context decision exposes the pills', () => {
    it('patient-focused context includes name-interpolated pills', () => {
      const ctx = deriveAgentContext({
        focusPatientId: 'pat-1',
        focusPatientName: 'Eduardo Perez',
        hasPanelSurface: true,
      });
      expect(ctx.patientPromptPills).toHaveLength(3);
      expect(ctx.patientPromptPills[0]?.label).toBe(
        'Get brief on Eduardo Perez',
      );
    });

    it('no-patient context still exposes the pill list (rendered as disabled)', () => {
      const ctx = deriveAgentContext({
        focusPatientId: '',
        hasPanelSurface: false,
      });
      expect(ctx.patientPromptPills).toHaveLength(3);
      expect(ctx.patientPromptPills[0]?.label).toBe('Get brief on patient');
      expect(ctx.patientPromptsEnabled).toBe(false);
    });

    it('panel-capable context exposes the pill list (disabled with reason)', () => {
      const ctx = deriveAgentContext({
        focusPatientId: '',
        hasPanelSurface: true,
      });
      expect(ctx.patientPromptPills).toHaveLength(3);
      expect(ctx.patientPromptsEnabled).toBe(false);
      expect(ctx.patientPromptDisabledReason).toMatch(/select a patient/i);
    });
  });
});
