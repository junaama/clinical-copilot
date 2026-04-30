/**
 * Initial welcome state — two primary chips that send canonical questions to
 * the agent. Mirrors the prototype's `Welcome` component.
 */

import type { JSX } from 'react';

export interface SuggestionChip {
  readonly id: string;
  readonly label: string;
  readonly icon: string;
}

export const SUGGESTIONS: readonly SuggestionChip[] = [
  { id: 'attention', label: 'Who needs attention first?', icon: '◐' },
  { id: 'overnight', label: 'What happened overnight?', icon: '☾' },
];

interface WelcomeProps {
  readonly patientName: string;
  readonly onPick: (label: string) => void;
}

export function Welcome({ patientName, onPick }: WelcomeProps): JSX.Element {
  return (
    <div className="agent-welcome">
      <div className="agent-welcome-eyebrow">Good morning</div>
      <h3>How can I help with this chart?</h3>
      <p>
        I read {patientName}'s record over FHIR — I won't write orders or notes
        without your confirmation.
      </p>
      <div className="agent-chips">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.id}
            className="agent-chip primary"
            onClick={() => onPick(s.label)}
          >
            <span className="agent-chip-icon">{s.icon}</span>
            <span>{s.label}</span>
          </button>
        ))}
      </div>
      <div className="agent-welcome-meta">Last sync · just now</div>
    </div>
  );
}
