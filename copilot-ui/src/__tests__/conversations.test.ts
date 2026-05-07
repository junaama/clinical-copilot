/**
 * conversations.ts — parser coverage for /conversations/:id/messages
 * (issue 045).
 *
 * The endpoint may return assistant rows with structured ``block`` and
 * ``route`` metadata (post-issue-045 turn store), or plain rows with only
 * ``content`` (legacy fallback / anonymous flows). The parser must accept
 * both shapes and degrade gracefully on malformed payloads — a single bad
 * agent row should not blank the whole conversation.
 */

import { describe, expect, it } from 'vitest';
import { parseConversationMessagesResponse } from '../api/conversations';

describe('parseConversationMessagesResponse — issue 045 rehydration', () => {
  it('parses user + agent rows with structured block + route', () => {
    const raw = {
      id: 'conv-a',
      title: 'Robert overnight brief',
      last_focus_pid: 'pat-robert',
      messages: [
        { role: 'user', content: 'How is Robert?' },
        {
          role: 'agent',
          content: 'Robert is stable.',
          block: {
            kind: 'plain',
            lead: 'Robert is stable.',
            citations: [
              {
                card: 'vitals',
                label: 'BP 120/76',
                fhir_ref: 'Observation/obs-1',
              },
            ],
            followups: ['Show overnight events'],
          },
          route: { kind: 'chart', label: 'Reading the patient record' },
          diagnostics: { decision: 'allow', supervisor_action: '' },
          workflow_id: 'W-2',
          classifier_confidence: 0.9,
        },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    expect(parsed).not.toBeNull();
    expect(parsed!.id).toBe('conv-a');
    expect(parsed!.messages).toHaveLength(2);

    const [user, agent] = parsed!.messages;
    expect(user.role).toBe('user');
    expect(user.content).toBe('How is Robert?');

    expect(agent.role).toBe('agent');
    expect(agent.block).toBeDefined();
    expect(agent.block!.kind).toBe('plain');
    expect(agent.block!.citations).toHaveLength(1);
    expect(agent.block!.citations[0].fhir_ref).toBe('Observation/obs-1');

    expect(agent.route).toEqual({
      kind: 'chart',
      label: 'Reading the patient record',
    });
    expect(agent.diagnostics).toEqual({
      decision: 'allow',
      supervisor_action: '',
    });
    expect(agent.workflow_id).toBe('W-2');
    expect(agent.classifier_confidence).toBe(0.9);
  });

  it('parses a triage block + panel route', () => {
    const raw = {
      id: 'conv-a',
      title: '',
      last_focus_pid: '',
      messages: [
        { role: 'user', content: 'who needs me?' },
        {
          role: 'agent',
          content: 'Three patients need attention.',
          block: {
            kind: 'triage',
            lead: 'Three patients need attention.',
            cohort: [
              {
                id: 'pat-1',
                name: 'Robert Hayes',
                age: 67,
                room: '302',
                score: 88,
                trend: 'up',
                reasons: ['RR up'],
                self: false,
                fhir_ref: 'Patient/pat-1',
              },
            ],
            citations: [],
            followups: [],
          },
          route: { kind: 'panel', label: 'Reviewing your panel' },
        },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    expect(parsed).not.toBeNull();
    const agent = parsed!.messages[1];
    expect(agent.block?.kind).toBe('triage');
    if (agent.block?.kind === 'triage') {
      expect(agent.block.cohort).toHaveLength(1);
      expect(agent.block.cohort[0].name).toBe('Robert Hayes');
    }
    expect(agent.route?.kind).toBe('panel');
  });

  it('parses an overnight block with deltas + timeline', () => {
    const raw = {
      id: 'conv-a',
      title: '',
      last_focus_pid: '',
      messages: [
        {
          role: 'agent',
          content: 'Overnight summary',
          block: {
            kind: 'overnight',
            lead: 'Overnight summary',
            deltas: [{ label: 'Tmax', from: '37.0', to: '38.4', dir: 'up' }],
            timeline: [
              {
                t: '22:14',
                kind: 'Nursing note',
                text: 'Increased confusion',
                fhir_ref: null,
              },
            ],
            citations: [],
            followups: [],
          },
          route: { kind: 'chart', label: 'Reading the patient record' },
        },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    const agent = parsed!.messages[0];
    expect(agent.block?.kind).toBe('overnight');
    if (agent.block?.kind === 'overnight') {
      expect(agent.block.deltas).toHaveLength(1);
      expect(agent.block.timeline).toHaveLength(1);
    }
  });

  it('legacy: agent rows with only content (no block/route) parse cleanly', () => {
    /** AC: legacy conversations without structured metadata still render
     *  safely as plain text. The parser must not throw on missing fields,
     *  and the returned shape leaves block/route undefined so the rehydration
     *  path falls back to a synthesized plain block. */
    const raw = {
      id: 'conv-legacy',
      title: 'old thread',
      last_focus_pid: '',
      messages: [
        { role: 'user', content: 'old question' },
        { role: 'agent', content: 'old answer' },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    expect(parsed).not.toBeNull();
    const [, agent] = parsed!.messages;
    expect(agent.role).toBe('agent');
    expect(agent.content).toBe('old answer');
    expect(agent.block).toBeUndefined();
    expect(agent.route).toBeUndefined();
  });

  it('malformed block on an agent row degrades to plain (block undefined)', () => {
    /** A single bad agent row should not break the whole conversation
     *  rehydration — the parser swallows the failure and returns the row
     *  with content present and block undefined. */
    const raw = {
      id: 'conv-a',
      title: '',
      last_focus_pid: '',
      messages: [
        {
          role: 'agent',
          content: 'something',
          block: { kind: 'unknown-kind', lead: 'x' },
        },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    expect(parsed).not.toBeNull();
    expect(parsed!.messages[0].content).toBe('something');
    expect(parsed!.messages[0].block).toBeUndefined();
  });

  it('malformed route on an agent row degrades to no-route', () => {
    const raw = {
      id: 'conv-a',
      title: '',
      last_focus_pid: '',
      messages: [
        {
          role: 'agent',
          content: 'reply',
          route: { kind: 'made-up-kind', label: 'x' },
        },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    expect(parsed!.messages[0].route).toBeUndefined();
  });

  it('skips rows with unknown roles instead of leaking them as user/agent', () => {
    const raw = {
      id: 'conv-a',
      title: '',
      last_focus_pid: '',
      messages: [
        { role: 'system', content: 'should be skipped' },
        { role: 'user', content: 'real user message' },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    expect(parsed!.messages).toHaveLength(1);
    expect(parsed!.messages[0].role).toBe('user');
  });

  it('returns null when the response shape is broken at the top level', () => {
    expect(parseConversationMessagesResponse(null)).toBeNull();
    expect(parseConversationMessagesResponse('garbage')).toBeNull();
    expect(parseConversationMessagesResponse({ messages: [] })).toBeNull();
  });

  it('handles empty messages list', () => {
    const raw = {
      id: 'conv-empty',
      title: '',
      last_focus_pid: '',
      messages: [],
    };
    const parsed = parseConversationMessagesResponse(raw);
    expect(parsed).not.toBeNull();
    expect(parsed!.messages).toHaveLength(0);
  });

  it('rehydrates a refusal route (issue 042 panel-failure) verbatim', () => {
    /** A W-1 fail-closed turn keeps kind=panel with the unavailable label,
     *  so the badge accurately advertises the route the system tried. */
    const raw = {
      id: 'conv-a',
      title: '',
      last_focus_pid: '',
      messages: [
        {
          role: 'agent',
          content: 'Panel data is unavailable…',
          block: {
            kind: 'plain',
            lead: 'Panel data is unavailable…',
            citations: [],
            followups: [],
          },
          route: { kind: 'panel', label: 'Panel data unavailable' },
          diagnostics: {
            decision: 'tool_failure',
            supervisor_action: 'run_panel_triage',
          },
        },
      ],
    };
    const parsed = parseConversationMessagesResponse(raw);
    const agent = parsed!.messages[0];
    expect(agent.route).toEqual({
      kind: 'panel',
      label: 'Panel data unavailable',
    });
    expect(agent.diagnostics?.decision).toBe('tool_failure');
  });
});
