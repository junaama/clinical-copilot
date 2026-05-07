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

describe('parseChatResponse — diagnostics (issue 042)', () => {
  it('parses a diagnostics object with decision + supervisor_action', () => {
    const ok = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        diagnostics: { decision: 'allow', supervisor_action: 'extract' },
      },
    };
    const result = parseChatResponse(ok);
    expect(result.state.diagnostics.decision).toBe('allow');
    expect(result.state.diagnostics.supervisor_action).toBe('extract');
  });

  it('accepts empty-string diagnostic fields (not-set sentinel)', () => {
    // Backend uses '' to signal "not set this turn" — clarify and chart
    // turns have no supervisor_action, for example.
    const ok = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        diagnostics: { decision: '', supervisor_action: '' },
      },
    };
    const result = parseChatResponse(ok);
    expect(result.state.diagnostics.decision).toBe('');
    expect(result.state.diagnostics.supervisor_action).toBe('');
  });

  it('rejects a missing diagnostics field', () => {
    const bad = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        patient_id: '4',
        workflow_id: 'W-2',
        classifier_confidence: 0.9,
        message_count: 1,
        route: { kind: 'chart', label: 'Reading the patient record' },
        // diagnostics deliberately omitted
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/diagnostics/);
  });

  it('rejects a non-object diagnostics', () => {
    const bad = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        diagnostics: 'allow',
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/state.diagnostics/);
  });

  it('rejects a non-string decision', () => {
    const bad = {
      ...MOCK_OVERNIGHT_RESPONSE,
      state: {
        ...MOCK_OVERNIGHT_RESPONSE.state,
        diagnostics: { decision: 42, supervisor_action: '' },
      },
    };
    expect(() => parseChatResponse(bad)).toThrow(/diagnostics.decision/);
  });

  it('parses a panel-unavailable failure response with diagnostics', () => {
    // The full wire shape on a panel triage failure: panel kind +
    // ``Panel data unavailable`` label + tool_failure decision.
    const failureBlock = {
      kind: 'plain' as const,
      lead:
        "Panel data is unavailable right now, so I can't rank the " +
        'patients on your panel.',
      citations: [],
      followups: [],
    };
    const ok = {
      conversation_id: 'demo-panel-fail',
      reply: failureBlock.lead,
      block: failureBlock,
      state: {
        patient_id: 'fixture-1',
        workflow_id: 'W-1',
        classifier_confidence: 0.93,
        message_count: 2,
        route: { kind: 'panel', label: 'Panel data unavailable' },
        diagnostics: { decision: 'tool_failure', supervisor_action: '' },
      },
    };
    const result = parseChatResponse(ok);
    expect(result.state.route.kind).toBe('panel');
    expect(result.state.route.label).toBe('Panel data unavailable');
    expect(result.state.diagnostics.decision).toBe('tool_failure');
  });
});
