import type { JSX } from 'react';
import type { Delta } from '../api/types';

interface DeltaGridProps {
  readonly deltas: readonly Delta[];
}

export function DeltaGrid({ deltas }: DeltaGridProps): JSX.Element {
  return (
    <div className="agent-deltas">
      {deltas.map((d, i) => (
        <div key={i} className={`agent-delta ${d.dir}`}>
          <div className="agent-delta-lbl">{d.label}</div>
          <div className="agent-delta-vals">
            <span className="from">{d.from}</span>
            <span className="arrow">→</span>
            <span className="to">{d.to}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
