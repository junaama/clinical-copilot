import type { JSX } from 'react';
import type { CohortRow } from '../api/types';
import { ScoreBar } from './ScoreBar';

interface CohortBlockProps {
  readonly cohort: readonly CohortRow[];
  readonly onJumpToVitals: () => void;
}

export function CohortBlock({ cohort, onJumpToVitals }: CohortBlockProps): JSX.Element {
  return (
    <div className="agent-cohort">
      {cohort.map((p, idx) => (
        <div key={p.id} className={'agent-pat ' + (p.self ? 'self' : '')}>
          <div className="agent-pat-rank">{idx + 1}</div>
          <div className="agent-pat-body">
            <div className="agent-pat-hd">
              <span className="agent-pat-name">{p.name}</span>
              <span className="agent-pat-meta">
                {p.age}y · {p.room}
              </span>
              <ScoreBar score={p.score} trend={p.trend} />
            </div>
            <ul className="agent-pat-reasons">
              {p.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
            {p.self && (
              <button className="agent-pat-jump" onClick={onJumpToVitals}>
                Jump to vitals →
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
