import type { JSX } from 'react';

interface LauncherProps {
  readonly onClick: () => void;
  readonly surface: 'panel' | 'floating' | 'inline';
}

export function Launcher({ onClick, surface }: LauncherProps): JSX.Element {
  return (
    <button
      className={`agent-launcher ${surface}`}
      onClick={onClick}
      aria-label="Open chart agent"
    >
      <span className="agent-launcher-mark">
        <span />
      </span>
      <span className="agent-launcher-txt">Ask about this chart</span>
      <kbd>⌘K</kbd>
    </button>
  );
}
