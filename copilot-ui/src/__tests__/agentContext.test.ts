import { describe, expect, it } from 'vitest';
import { deriveAgentContext } from '../lib/agentContext';

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
});
