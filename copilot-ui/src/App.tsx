/**
 * Top-level app — wires SMART launch params, Tweaks, and the AgentPanel.
 *
 * Cross-frame contract:
 *   • emits `copilot:flash-card` when a citation chip is clicked, so the host
 *     OpenEMR window can flash the matching chart card.
 *   • the host posts the SMART launch params as URL query/hash; we read them
 *     once on first paint and forward to the agent service.
 */

import { useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { AgentPanel, type ChatMessage, type Density, type Surface } from './components/AgentPanel';
import { Launcher } from './components/Launcher';
import { TweaksPanel } from './components/Tweaks/TweaksPanel';
import { TweakButton, TweakColor, TweakRadio, TweakSection, TweakToggle } from './components/Tweaks/controls';
import { useTweaks, type TweakValues } from './components/Tweaks/useTweaks';
import { parseSmartLaunch, type SmartLaunchContext } from './api/smart';
import type { Citation } from './api/types';

interface CopilotTweaks extends TweakValues {
  readonly surface: Surface;
  readonly density: Density;
  readonly showCitations: boolean;
  readonly proactive: boolean;
  readonly accent: string;
}

const TWEAK_DEFAULTS: CopilotTweaks = {
  surface: 'panel',
  density: 'regular',
  showCitations: true,
  proactive: false,
  accent: '#4abfac',
};

const DEFAULT_PATIENT_NAME = 'this patient';

// In dev / fixture mode the SMART launch may not be present; we still want a
// usable conversation_id that's stable across re-renders.
function makeConversationId(): string {
  return `conv-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function App(): JSX.Element {
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [open, setOpen] = useState<boolean>(true);
  const [messages, setMessages] = useState<readonly ChatMessage[]>([]);
  const [highlights, setHighlights] = useState<Record<string, boolean>>({});
  const conversationIdRef = useRef<string>(makeConversationId());

  const smart: SmartLaunchContext = useMemo(
    () => parseSmartLaunch(window.location.href),
    [],
  );

  // ⌘K / Ctrl-K toggles the panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function onCite(citation: Citation): void {
    // 1. Local in-frame flash (so demo without a host parent still shows feedback).
    setHighlights({ [citation.card]: true });
    window.setTimeout(() => setHighlights({}), 1700);

    // 2. Cross-frame postMessage so the host OpenEMR window can flash the
    //    chart card on its side. The host subscribes to `copilot:flash-card`.
    try {
      window.parent.postMessage(
        {
          type: 'copilot:flash-card',
          card: citation.card,
          fhir_ref: citation.fhir_ref,
        },
        '*',
      );
    } catch {
      // ignore — non-iframe context
    }

    // 3. Local DOM scroll (works in standalone preview where chart cards live
    //    in this document via [data-card]).
    const el = document.querySelector(`[data-card="${citation.card}"]`);
    if (el instanceof HTMLElement) {
      const r = el.getBoundingClientRect();
      if (r.top < 80 || r.bottom > window.innerHeight - 20) {
        window.scrollTo({ top: window.scrollY + r.top - 120, behavior: 'smooth' });
      }
    }
  }

  // Apply the highlight class to local cards if they exist (standalone mode).
  useEffect(() => {
    const cards = document.querySelectorAll<HTMLElement>('[data-card]');
    cards.forEach((el) => {
      const card = el.dataset['card'] ?? '';
      if (highlights[card] === true) el.classList.add('flash');
      else el.classList.remove('flash');
    });
  }, [highlights]);

  const surface = tweaks.surface as Surface;
  const density = tweaks.density as Density;
  const showCitations = tweaks.showCitations === true;
  const accent = String(tweaks.accent);

  const patientName = smart.patientId ? `Patient/${smart.patientId}` : DEFAULT_PATIENT_NAME;

  return (
    <>
      <AgentPanel
        open={open}
        surface={surface}
        density={density}
        showCitations={showCitations}
        accent={accent}
        conversationId={conversationIdRef.current}
        patientId={smart.patientId ?? ''}
        userId={smart.userId ?? ''}
        smartAccessToken={smart.accessToken}
        patientName={patientName}
        messages={messages}
        setMessages={(updater) => setMessages((prev) => updater(prev))}
        onClose={() => setOpen(false)}
        onCite={onCite}
      />

      {!open && <Launcher onClick={() => setOpen(true)} surface={surface} />}

      <TweaksPanel title="Tweaks">
        <TweakSection label="Agent" />
        <TweakRadio
          label="Surface"
          value={surface}
          options={[
            { value: 'panel', label: 'Panel' },
            { value: 'floating', label: 'Float' },
            { value: 'inline', label: 'Inline' },
          ]}
          onChange={(v) => setTweak('surface', v)}
        />
        <TweakRadio
          label="Density"
          value={density}
          options={['compact', 'regular', 'comfy']}
          onChange={(v) => setTweak('density', v)}
        />
        <TweakToggle
          label="Show citations"
          value={showCitations}
          onChange={(v) => setTweak('showCitations', v)}
        />
        <TweakToggle
          label="Auto-summary on chart open"
          value={tweaks.proactive}
          onChange={(v) => {
            setTweak('proactive', v);
            setMessages([]);
          }}
        />
        <TweakSection label="Theme" />
        <TweakColor label="Accent" value={accent} onChange={(v) => setTweak('accent', v)} />
        <TweakSection label="Demo" />
        <TweakButton
          label="Reset conversation"
          secondary
          onClick={() => {
            setMessages([]);
            conversationIdRef.current = makeConversationId();
          }}
        />
      </TweaksPanel>
    </>
  );
}
