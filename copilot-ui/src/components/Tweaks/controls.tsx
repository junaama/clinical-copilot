/**
 * Tweak form controls — radio (segmented), toggle, color, button, section.
 * Ported from tweaks-panel.jsx; behaviour preserved.
 */

import {
  useRef,
  useState,
  type CSSProperties,
  type JSX,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
} from 'react';

interface RowProps {
  readonly label: string;
  readonly value?: string;
  readonly inline?: boolean;
  readonly children: ReactNode;
}

function TweakRow({ label, value, inline = false, children }: RowProps): JSX.Element {
  return (
    <div className={inline ? 'twk-row twk-row-h' : 'twk-row'}>
      <div className="twk-lbl">
        <span>{label}</span>
        {value !== undefined && <span className="twk-val">{value}</span>}
      </div>
      {children}
    </div>
  );
}

interface SectionProps {
  readonly label: string;
  readonly children?: ReactNode;
}

export function TweakSection({ label, children }: SectionProps): JSX.Element {
  return (
    <>
      <div className="twk-sect">{label}</div>
      {children}
    </>
  );
}

export interface RadioOption {
  readonly value: string;
  readonly label: string;
}

interface RadioProps {
  readonly label: string;
  readonly value: string;
  readonly options: readonly (string | RadioOption)[];
  readonly onChange: (value: string) => void;
}

export function TweakRadio({
  label,
  value,
  options,
  onChange,
}: RadioProps): JSX.Element {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [dragging, setDragging] = useState<boolean>(false);
  const opts: readonly RadioOption[] = options.map((o) =>
    typeof o === 'object' ? o : { value: o, label: o },
  );
  const idx = Math.max(
    0,
    opts.findIndex((o) => o.value === value),
  );
  const n = opts.length;

  const valueRef = useRef<string>(value);
  valueRef.current = value;

  const segAt = (clientX: number): string => {
    if (!trackRef.current) return value;
    const r = trackRef.current.getBoundingClientRect();
    const inner = r.width - 4;
    const i = Math.floor(((clientX - r.left - 2) / inner) * n);
    return opts[Math.max(0, Math.min(n - 1, i))]!.value;
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>): void => {
    setDragging(true);
    const v0 = segAt(e.clientX);
    if (v0 !== valueRef.current) onChange(v0);
    const move = (ev: globalThis.PointerEvent): void => {
      if (!trackRef.current) return;
      const v = segAt(ev.clientX);
      if (v !== valueRef.current) onChange(v);
    };
    const up = (): void => {
      setDragging(false);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const thumbStyle: CSSProperties = {
    left: `calc(2px + ${idx} * (100% - 4px) / ${n})`,
    width: `calc((100% - 4px) / ${n})`,
  };

  return (
    <TweakRow label={label}>
      <div
        ref={trackRef}
        role="radiogroup"
        onPointerDown={onPointerDown}
        className={dragging ? 'twk-seg dragging' : 'twk-seg'}
      >
        <div className="twk-seg-thumb" style={thumbStyle} />
        {opts.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={o.value === value}
          >
            {o.label}
          </button>
        ))}
      </div>
    </TweakRow>
  );
}

interface ToggleProps {
  readonly label: string;
  readonly value: boolean;
  readonly onChange: (value: boolean) => void;
}

export function TweakToggle({ label, value, onChange }: ToggleProps): JSX.Element {
  return (
    <div className="twk-row twk-row-h">
      <div className="twk-lbl">
        <span>{label}</span>
      </div>
      <button
        type="button"
        className="twk-toggle"
        data-on={value ? '1' : '0'}
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
      >
        <i />
      </button>
    </div>
  );
}

interface ColorProps {
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
}

export function TweakColor({ label, value, onChange }: ColorProps): JSX.Element {
  return (
    <div className="twk-row twk-row-h">
      <div className="twk-lbl">
        <span>{label}</span>
      </div>
      <input
        type="color"
        className="twk-swatch"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

interface ButtonProps {
  readonly label: string;
  readonly onClick: (e: MouseEvent<HTMLButtonElement>) => void;
  readonly secondary?: boolean;
}

export function TweakButton({
  label,
  onClick,
  secondary = false,
}: ButtonProps): JSX.Element {
  return (
    <button
      type="button"
      className={secondary ? 'twk-btn secondary' : 'twk-btn'}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
