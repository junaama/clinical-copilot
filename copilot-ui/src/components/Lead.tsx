/**
 * Typewriter render of the block lead. Streams characters at ~14ms cadence,
 * matching the prototype's pacing. When `streaming` flips false, the rest of
 * the block paints in (handled by AgentMsg).
 */

import { useEffect, useState, type JSX } from 'react';

interface LeadProps {
  readonly text: string;
  readonly streaming: boolean;
}

const STREAM_CHARS_PER_TICK = 3;
const STREAM_TICK_MS = 14;

export function Lead({ text, streaming }: LeadProps): JSX.Element {
  const [shown, setShown] = useState<string>(streaming ? '' : text);

  useEffect(() => {
    if (!streaming) {
      setShown(text);
      return undefined;
    }
    let i = 0;
    setShown('');
    const id = window.setInterval(() => {
      i += STREAM_CHARS_PER_TICK;
      setShown(text.slice(0, i));
      if (i >= text.length) window.clearInterval(id);
    }, STREAM_TICK_MS);
    return () => window.clearInterval(id);
  }, [text, streaming]);

  return (
    <p className="agent-lead">
      {shown}
      {streaming && <span className="agent-caret">▍</span>}
    </p>
  );
}
