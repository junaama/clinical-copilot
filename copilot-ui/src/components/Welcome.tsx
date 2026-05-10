/**
 * Initial welcome state — splits suggestion chips into patient-specific and
 * panel-wide groups (issue 043, story 17–21).
 *
 * Issue 044 evolves the patient-specific group into three contextual prompt
 * pills (brief, medications, overnight-trends) sourced from
 * ``context.patientPromptPills``. The patient-focused context interpolates
 * the resolved patient name into each pill; no-patient and panel-capable
 * contexts render the same pills as disabled affordances with a reason, so
 * the affordance is consistently visible.
 */

import type { JSX } from 'react';
import type { AgentContextDecision, PromptPill } from '../lib/agentContext';

export interface SuggestionChip {
  readonly id: string;
  readonly label: string;
  readonly icon: string;
  /** Optional text sent on click. Defaults to ``label`` when absent. */
  readonly promptText?: string;
}

// Temporarily disabled: the "Who needs attention first?" API path is not
// reliable enough for the agent panel shortcut yet. Keep the definition close
// to the render site so the pill can be restored once the backend path is
// fixed.
// export const PANEL_SUGGESTIONS: readonly SuggestionChip[] = [
//   { id: 'attention', label: 'Who needs attention first?', icon: '◐' },
// ];

interface WelcomeProps {
  readonly context: AgentContextDecision;
  /** Receives the prompt text sent to /chat. For patient pills this is the
   *  pill's ``promptText``; for panel chips it's the chip ``label``. */
  readonly onPick: (promptText: string) => void;
}

export function Welcome({ context, onPick }: WelcomeProps): JSX.Element {
  return (
    <div className="agent-welcome" data-context-kind={context.kind}>
      <div className="agent-welcome-eyebrow">Good morning</div>
      <h3>{context.welcomeHeadline}</h3>
      <p>{context.welcomeSubcopy}</p>
      <div className="agent-chips">
        {/* Temporarily disabled with PANEL_SUGGESTIONS above.
        {PANEL_SUGGESTIONS.map((s) => (
          <SuggestionButton
            key={s.id}
            chip={s}
            enabled={context.panelPromptsEnabled}
            disabledReason={context.panelPromptDisabledReason}
            kind="panel"
            onPick={onPick}
          />
        ))} */}
        {context.patientPromptPills.map((pill: PromptPill) => (
          <SuggestionButton
            key={pill.id}
            chip={{
              id: pill.id,
              label: pill.label,
              icon: pill.icon,
              promptText: pill.promptText,
            }}
            enabled={context.patientPromptsEnabled}
            disabledReason={context.patientPromptDisabledReason}
            kind="patient"
            onPick={onPick}
          />
        ))}
      </div>
      <div className="agent-welcome-meta">Last sync · just now</div>
    </div>
  );
}

interface SuggestionButtonProps {
  readonly chip: SuggestionChip;
  readonly enabled: boolean;
  readonly disabledReason: string | null;
  readonly kind: 'patient' | 'panel';
  readonly onPick: (promptText: string) => void;
}

function SuggestionButton({
  chip,
  enabled,
  disabledReason,
  kind,
  onPick,
}: SuggestionButtonProps): JSX.Element {
  const reason = enabled ? null : disabledReason;
  return (
    <button
      type="button"
      className="agent-chip primary"
      data-suggestion-kind={kind}
      data-suggestion-id={chip.id}
      onClick={() => {
        if (enabled) onPick(chip.promptText ?? chip.label);
      }}
      disabled={!enabled}
      aria-disabled={!enabled}
      title={reason ?? undefined}
    >
      <span className="agent-chip-icon" aria-hidden="true">{chip.icon}</span>
      <span>{chip.label}</span>
      {reason ? (
        <span
          className="agent-chip-hint"
          data-testid={`suggestion-hint-${chip.id}`}
        >
          {reason}
        </span>
      ) : null}
    </button>
  );
}
