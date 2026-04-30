import type { JSX } from 'react';

export function Thinking(): JSX.Element {
  return (
    <div className="agent-msg agent">
      <div className="agent-bubble agent thinking">
        <span className="agent-think">
          <i />
          <i />
          <i />
        </span>
        <span className="agent-think-lbl">Reading chart…</span>
      </div>
    </div>
  );
}
