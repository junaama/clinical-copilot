import type { JSX } from 'react';
import type { TimelineEvent } from '../api/types';

interface TimelineProps {
  readonly events: readonly TimelineEvent[];
}

function classFor(kind: string): string {
  return 'k-' + kind.toLowerCase().replace(/\s/g, '-');
}

export function Timeline({ events }: TimelineProps): JSX.Element {
  return (
    <div className="agent-timeline">
      {events.map((e, i) => (
        <div key={i} className="agent-tle">
          <div className="agent-tle-t mono">{e.t}</div>
          <div className="agent-tle-dot" />
          <div className="agent-tle-body">
            <span className={`agent-tle-kind ${classFor(e.kind)}`}>{e.kind}</span>
            <span className="agent-tle-text">{e.text}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
