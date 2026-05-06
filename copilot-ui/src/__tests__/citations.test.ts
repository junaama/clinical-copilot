/**
 * Tests for `planCitationClick` (issue 027).
 *
 * These pin the contract that guideline citations are non-chart and do
 * not trigger chart-card highlight / postMessage behavior.
 */

import { describe, expect, it } from 'vitest';
import { planCitationClick } from '../api/citations';
import type { Citation } from '../api/types';

describe('planCitationClick', () => {
  it('returns a chart-card effect for chart citations', () => {
    const citation: Citation = {
      card: 'vitals',
      label: 'Vitals · last 4 readings',
      fhir_ref: 'Observation/obs-1',
    };
    const effect = planCitationClick(citation);
    expect(effect).toEqual({
      kind: 'chart-card',
      card: 'vitals',
      fhir_ref: 'Observation/obs-1',
    });
  });

  it.each([
    'labs',
    'medications',
    'problems',
    'allergies',
    'prescriptions',
    'encounters',
    'documents',
    'other',
  ] as const)('routes %s through the chart-card path', (card) => {
    const citation: Citation = { card, label: 'x', fhir_ref: null };
    expect(planCitationClick(citation).kind).toBe('chart-card');
  });

  it('returns a noop effect for guideline citations', () => {
    const citation: Citation = {
      card: 'guideline',
      label: 'ADA · 6.5',
      fhir_ref: 'guideline:ada-a1c-2024-1',
    };
    const effect = planCitationClick(citation);
    expect(effect).toEqual({ kind: 'noop', reason: 'guideline' });
  });

  it('preserves fhir_ref of null on chart-card effects', () => {
    const citation: Citation = {
      card: 'other',
      label: 'misc',
      fhir_ref: null,
    };
    const effect = planCitationClick(citation);
    expect(effect.kind).toBe('chart-card');
    if (effect.kind !== 'chart-card') throw new Error('unreachable');
    expect(effect.fhir_ref).toBeNull();
  });
});
