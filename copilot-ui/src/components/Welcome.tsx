/**
 * Initial welcome state — splits suggestion chips into patient-specific and
 * panel-wide groups (issue 043, story 17–21). The agent context decides
 * which copy and which chips to render or disable; this component is the
 * single rendering surface for the welcome state.
 */

import type { JSX } from 'react';
import type { AgentContextDecision } from '../lib/agentContext';

export interface SuggestionChip {
  readonly id: string;
  readonly label: string;
  readonly icon: string;
}

/** Chart / patient-context prompts. Disabled with a reason in the
 *  no-patient and panel-capable contexts. */
export const PATIENT_SUGGESTIONS: readonly SuggestionChip[] = [
  { id: 'overnight', label: 'What happened overnight?', icon: '☾' },
];

/** Panel-wide prompts that the W-1 panel route can answer without a
 *  selected patient. Disabled when the surrounding shell does not mount
 *  a panel surface. */
export const PANEL_SUGGESTIONS: readonly SuggestionChip[] = [
  { id: 'attention', label: 'Who needs attention first?', icon: '◐' },
];

interface WelcomeProps {
  readonly context: AgentContextDecision;
  readonly onPick: (label: string) => void;
}

export function Welcome({ context, onPick }: WelcomeProps): JSX.Element {
  return (
    <div className="agent-welcome" data-context-kind={context.kind}>
      <div className="agent-welcome-eyebrow">Good morning</div>
      <h3>{context.welcomeHeadline}</h3>
      <p>{context.welcomeSubcopy}</p>
      <div className="agent-chips">
        {PANEL_SUGGESTIONS.map((s) => (
          <SuggestionButton
            key={s.id}
            chip={s}
            enabled={context.panelPromptsEnabled}
            disabledReason={context.panelPromptDisabledReason}
            kind="panel"
            onPick={onPick}
          />
        ))}
        {PATIENT_SUGGESTIONS.map((s) => (
          <SuggestionButton
            key={s.id}
            chip={s}
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
  readonly onPick: (label: string) => void;
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
        if (enabled) onPick(chip.label);
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
