import type { JSX } from 'react';

interface UserMsgProps {
  readonly text: string;
  readonly auto?: boolean;
}

export function UserMsg({ text, auto = false }: UserMsgProps): JSX.Element {
  return (
    <div className="agent-msg user">
      <div className="agent-bubble user">{text}</div>
      {auto && <div className="agent-auto-tag">auto-asked on chart open</div>}
    </div>
  );
}
