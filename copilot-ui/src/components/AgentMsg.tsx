/**
 * AgentMsg — renders one agent reply, dispatching on `block.kind`.
 *
 * The Lead typewriter runs over `block.lead`; everything else (cohort, deltas,
 * timeline, citations, followups) paints in once `streaming` flips false. This
 * is the testable boundary — block dispatch logic lives here.
 */

import type { JSX } from 'react';
import type {
  Block,
  ChatDiagnostics,
  ChatRoute,
  Citation,
  CitationCard,
} from '../api/types';
import { Lead } from './Lead';
import { CohortBlock } from './CohortBlock';
import { DeltaGrid } from './DeltaGrid';
import { Timeline } from './Timeline';

/** Per-turn metadata exposed behind the Technical details ``<details>`` —
 *  see ``TechnicalDetails`` below. Issue 042. */
export interface AgentDiagnostics {
  readonly route: ChatRoute;
  readonly workflow_id: string;
  readonly classifier_confidence: number;
  readonly diagnostics: ChatDiagnostics;
}

export interface AgentMessage {
  readonly role: 'agent';
  readonly block: Block;
  readonly streaming: boolean;
  readonly route?: ChatRoute;
  /** Issue 042: optional bag of per-turn diagnostic fields rendered behind
   *  a collapsed ``Technical details`` affordance. Absent on rehydrated
   *  turns (no state envelope is stored on the conversation row). */
  readonly debugInfo?: AgentDiagnostics;
}

export interface AgentErrorMessage {
  readonly role: 'agent-error';
  readonly status: number;
  readonly detail: string;
}

interface AgentMsgProps {
  readonly message: AgentMessage;
  readonly showCitations: boolean;
  readonly onCite: (citation: Citation) => void;
  readonly onFollowup: (label: string) => void;
  readonly onJumpToVitals: () => void;
}

export function AgentMsg({
  message,
  showCitations,
  onCite,
  onFollowup,
  onJumpToVitals,
}: AgentMsgProps): JSX.Element {
  const { block, streaming } = message;
  const showBody = !streaming;

  return (
    <div className="agent-msg agent">
      <div className="agent-bubble agent">
        {message.route && <RouteBadge route={message.route} />}
        <Lead text={block.lead} streaming={streaming} />
        {showBody && renderBody(block, onJumpToVitals)}
        {showBody && showCitations && block.citations.length > 0 && (
          <Citations citations={block.citations} onCite={onCite} />
        )}
        {showBody && message.debugInfo && (
          <TechnicalDetails info={message.debugInfo} />
        )}
      </div>
      {showBody && block.followups.length > 0 && (
        <div className="agent-followups">
          {block.followups.map((f, i) => (
            <button
              key={`${i}-${f}`}
              className="agent-chip ghost"
              onClick={() => onFollowup(f)}
            >
              {f}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function renderBody(
  block: Block,
  onJumpToVitals: () => void,
): JSX.Element | null {
  if (block.kind === 'triage') {
    return <CohortBlock cohort={block.cohort} onJumpToVitals={onJumpToVitals} />;
  }
  if (block.kind === 'overnight') {
    return (
      <>
        <DeltaGrid deltas={block.deltas} />
        <Timeline events={block.timeline} />
      </>
    );
  }
  if (block.kind === 'plain') {
    return null;
  }
  // Exhaustiveness — TypeScript verifies this is unreachable.
  assertNever(block);
}

function assertNever(value: never): never {
  throw new Error(`Unhandled block: ${JSON.stringify(value)}`);
}

interface RouteBadgeProps {
  readonly route: ChatRoute;
}

function RouteBadge({ route }: RouteBadgeProps): JSX.Element {
  return (
    <div
      className="agent-route"
      data-route-kind={route.kind}
      role="status"
      aria-label={`Route: ${route.label}`}
    >
      <span className="agent-route-dot" aria-hidden="true" />
      <span className="agent-route-label">{route.label}</span>
    </div>
  );
}

interface CitationsProps {
  readonly citations: readonly Citation[];
  readonly onCite: (citation: Citation) => void;
}

function Citations({ citations, onCite }: CitationsProps): JSX.Element {
  return (
    <div className="agent-cites">
      <span className="agent-cites-lbl">Sources</span>
      {citations.map((c, i) => (
        <button
          key={`${i}-${c.card}-${c.label}`}
          className="agent-cite"
          onClick={() => onCite(c)}
          data-card={c.card}
        >
          <span className="agent-cite-dot" />
          {c.label}
        </button>
      ))}
    </div>
  );
}

interface TechnicalDetailsProps {
  readonly info: AgentDiagnostics;
}

/**
 * Issue 042: collapsed ``<details>`` exposing per-turn route + diagnostic
 * fields for development and grading. Hidden by default — the disclosure
 * triangle has to be opened to see anything inside, so the clinical
 * answer remains uncluttered. The data-testid lets tests assert that the
 * panel is rendered at all (so peers can later add CSS to hide it
 * outside dev mode without breaking the contract test).
 */
function TechnicalDetails({ info }: TechnicalDetailsProps): JSX.Element {
  const { route, workflow_id, classifier_confidence, diagnostics } = info;
  const confidenceText = Number.isFinite(classifier_confidence)
    ? classifier_confidence.toFixed(2)
    : '—';
  return (
    <details
      className="agent-tech-details"
      data-testid="agent-technical-details"
    >
      <summary>Technical details</summary>
      <dl className="agent-tech-details-body">
        <dt>Route kind</dt>
        <dd data-field="route-kind">{route.kind}</dd>
        <dt>Route label</dt>
        <dd data-field="route-label">{route.label}</dd>
        <dt>Workflow</dt>
        <dd data-field="workflow-id">{workflow_id || '—'}</dd>
        <dt>Classifier confidence</dt>
        <dd data-field="classifier-confidence">{confidenceText}</dd>
        <dt>Decision</dt>
        <dd data-field="decision">{diagnostics.decision || '—'}</dd>
        <dt>Supervisor action</dt>
        <dd data-field="supervisor-action">
          {diagnostics.supervisor_action || '—'}
        </dd>
      </dl>
    </details>
  );
}

interface AgentErrorBubbleProps {
  readonly status: number;
  readonly detail: string;
}

export function AgentErrorBubble({
  status,
  detail,
}: AgentErrorBubbleProps): JSX.Element {
  const heading = status === 0 ? 'Network error' : `HTTP ${status}`;
  return (
    <div className="agent-msg agent">
      <div className="agent-bubble agent error">
        <p className="agent-lead">
          <strong>{heading}</strong> — {detail}
        </p>
      </div>
    </div>
  );
}

/** Re-export for cohort flash convenience. */
export type { CitationCard };
