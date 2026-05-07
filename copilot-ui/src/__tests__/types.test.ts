import { describe, expect, it } from 'vitest';
import { parseChatResponse } from '../api/types';
import {
  MOCK_OVERNIGHT_RESPONSE,
  MOCK_PLAIN_RESPONSE,
  MOCK_TRIAGE_RESPONSE,
} from '../fixtures/mockData';

describe('parseChatResponse', () => {
  it('parses a triage block round-trip', () => {
    const json = JSON.parse(JSON.stringify(MOCK_TRIAGE_RESPONSE));
    const result = parseChatResponse(json);
    expect(result.block.kind).toBe('triage');
    if (result.block.kind !== 'triage') throw new Error('unreachable');
    expect(result.block.cohort).toHaveLength(5);
    expect(result.block.cohort[0]?.self).toBe(true);
    expect(result.block.citations[0]?.card).toBe('vitals');
    expect(result.block.followups).toContain('Draft an SBAR for Wade235');
  });

  it('parses an overnight block round-trip', () => {
    const json = JSON.parse(JSON.stringify(MOCK_OVERNIGHT_RESPONSE));
    const result = parseChatResponse(json);
    expect(result.block.kind).toBe('overnight');
    if (result.block.kind !== 'overnight') throw new Error('unreachable');
    expect(result.block.deltas).toHaveLength(4);
    expect(result.block.timeline).toHaveLength(5);
    expect(result.block.timeline[0]?.t).toBe('22:14');
  });

  it('parses a plain block round-trip', () => {
    const json = JSON.parse(JSON.stringify(MOCK_PLAIN_RESPONSE));
    const result = parseChatResponse(json);
    expect(result.block.kind).toBe('plain');
    expect(result.block.lead.length).toBeGreaterThan(0);
  });

  it('rejects an unknown block.kind', () => {
    const bad = {
      ...MOCK_TRIAGE_RESPONSE,
      block: { ...MOCK_TRIAGE_RESPONSE.block, kind: 'mystery' },
    };
    expect(() => parseChatResponse(bad)).toThrow(/unknown block.kind/);
  });

  it('rejects a missing reply', () => {
    const bad = { ...MOCK_TRIAGE_RESPONSE, reply: '' };
    expect(() => parseChatResponse(bad)).toThrow(/reply/);
  });

  it('rejects a non-array cohort', () => {
    const bad = {
      ...MOCK_TRIAGE_RESPONSE,
      block: { ...MOCK_TRIAGE_RESPONSE.block, cohort: 'oops' },
    };
    expect(() => parseChatResponse(bad)).toThrow(/cohort/);
  });

  it('rejects an unknown citation card', () => {
    const bad = {
      ...MOCK_TRIAGE_RESPONSE,
      block: {
        ...MOCK_TRIAGE_RESPONSE.block,
        citations: [{ card: 'made-up', label: 'x', fhir_ref: null }],
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/citation card/);
  });

  it('rejects an invalid trend value', () => {
    const cohort0 = MOCK_TRIAGE_RESPONSE.block.kind === 'triage'
      ? MOCK_TRIAGE_RESPONSE.block.cohort[0]
      : null;
    if (!cohort0) throw new Error('test fixture invariant');
    const bad = {
      ...MOCK_TRIAGE_RESPONSE,
      block: {
        ...MOCK_TRIAGE_RESPONSE.block,
        cohort: [{ ...cohort0, trend: 'sideways' }],
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/trend/);
  });

  it('rejects a non-object response', () => {
    expect(() => parseChatResponse('nope')).toThrow();
    expect(() => parseChatResponse(null)).toThrow();
    expect(() => parseChatResponse([])).toThrow();
  });

  it('rejects a missing state.classifier_confidence', () => {
    const bad = {
      ...MOCK_TRIAGE_RESPONSE,
      state: { ...MOCK_TRIAGE_RESPONSE.state, classifier_confidence: 'high' },
    };
    expect(() => parseChatResponse(bad)).toThrow(/classifier_confidence/);
  });

  it('accepts a null fhir_ref on citations', () => {
    const triageBlock = MOCK_TRIAGE_RESPONSE.block;
    if (triageBlock.kind !== 'triage') throw new Error('test fixture invariant');
    const ok = {
      ...MOCK_TRIAGE_RESPONSE,
      block: {
        ...triageBlock,
        citations: [{ card: 'other', label: 'synthetic', fhir_ref: null }],
      },
    };
    const result = parseChatResponse(ok);
    expect(result.block.citations[0]?.fhir_ref).toBeNull();
  });

  it('accepts guideline citations on plain blocks (issue 027)', () => {
    const ok = {
      ...MOCK_PLAIN_RESPONSE,
      block: {
        ...MOCK_PLAIN_RESPONSE.block,
        citations: [
          {
            card: 'guideline',
            label: 'ADA · 6.5',
            fhir_ref: 'guideline:ada-a1c-2024-1',
          },
        ],
      },
    };
    const result = parseChatResponse(ok);
    expect(result.block.kind).toBe('plain');
    expect(result.block.citations[0]?.card).toBe('guideline');
    expect(result.block.citations[0]?.fhir_ref).toBe('guideline:ada-a1c-2024-1');
  });
});

describe('parseChatResponse — route metadata (issue 039)', () => {
  it('parses a chart route with a user-facing label', () => {
    const ok = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        route: { kind: 'chart', label: 'Reading the patient record' },
      },
    };
    const result = parseChatResponse(ok);
    expect(result.state.route.kind).toBe('chart');
    expect(result.state.route.label).toBe('Reading the patient record');
  });

  it('rejects a missing route', () => {
    const bad = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        patient_id: '4',
        workflow_id: 'W-2',
        classifier_confidence: 0.9,
        message_count: 1,
        // route deliberately omitted
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/route/);
  });

  it('rejects an unknown route kind', () => {
    const bad = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        route: { kind: 'mystery', label: 'x' },
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/route kind/);
  });

  it('rejects an empty route label', () => {
    const bad = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        route: { kind: 'chart', label: '' },
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/state.route.label/);
  });

  it('rejects a non-object route', () => {
    const bad = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        route: 'chart',
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/state.route/);
  });
});
