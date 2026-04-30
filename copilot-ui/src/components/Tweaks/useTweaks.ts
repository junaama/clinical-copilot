/**
 * useTweaks — single source of truth for Tweaks values.
 *
 * setTweak persists via the host (`__edit_mode_set_keys` → host rewrites the
 * EDITMODE block). Behaviour outside the design tool is purely cosmetic.
 */

import { useCallback, useState } from 'react';

export type TweakValue = string | number | boolean;
export type TweakValues = Readonly<Record<string, TweakValue>>;

export type SetTweak = {
  (key: string, value: TweakValue): void;
  (edits: Readonly<Record<string, TweakValue>>): void;
};

export function useTweaks<T extends TweakValues>(defaults: T): readonly [T, SetTweak] {
  const [values, setValues] = useState<T>(defaults);

  const setTweak = useCallback(
    ((keyOrEdits: string | Readonly<Record<string, TweakValue>>, val?: TweakValue): void => {
      const edits: Record<string, TweakValue> =
        typeof keyOrEdits === 'object' && keyOrEdits !== null
          ? { ...keyOrEdits }
          : { [keyOrEdits]: val as TweakValue };
      setValues((prev) => ({ ...prev, ...edits }) as T);
      try {
        window.parent.postMessage({ type: '__edit_mode_set_keys', edits }, '*');
      } catch {
        // postMessage failures are non-fatal — the UI still updates locally.
      }
    }) as SetTweak,
    [],
  );

  return [values, setTweak] as const;
}
