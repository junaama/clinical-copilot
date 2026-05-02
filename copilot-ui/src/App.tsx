/**
 * Top-level app — detects standalone vs EHR-launch mode.
 *
 * EHR-launch mode (SMART params in URL):
 *   Renders the original AgentPanel + Launcher + TweaksPanel.
 *
 * Standalone mode (no SMART params):
 *   Calls GET /me to detect session. Shows LoginPage on 401, AppShell +
 *   AgentPanel on 200.
 *
 * Cross-frame contract (EHR-launch only):
 *   - emits `copilot:flash-card` when a citation chip is clicked
 *   - reads SMART launch params from the URL once on first paint
 */

import { useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { AgentPanel, type ChatMessage, type Density, type Surface } from './components/AgentPanel';
import { AppShell } from './components/AppShell';
import { Launcher } from './components/Launcher';
import { LoginPage } from './components/LoginPage';
import { PanelView } from './components/PanelView';
import { TweaksPanel } from './components/Tweaks/TweaksPanel';
import { TweakButton, TweakColor, TweakRadio, TweakSection, TweakToggle } from './components/Tweaks/controls';
import { useTweaks, type TweakValues } from './components/Tweaks/useTweaks';
import { useSession } from './hooks/useSession';
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

function makeConversationId(): string {
  return `conv-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Detect whether SMART launch params are present in the URL (EHR-launch mode).
 */
function isEhrLaunch(smart: SmartLaunchContext): boolean {
  return !!(smart.conversationId || smart.patientId || smart.launch);
}

export function App(): JSX.Element {
  const smart: SmartLaunchContext = useMemo(
    () => parseSmartLaunch(window.location.href),
    [],
  );

  // If SMART launch params are in the URL, render the EHR-launch UI.
  // Otherwise, render the standalone login flow.
  if (isEhrLaunch(smart)) {
    return <EhrLaunchApp smart={smart} />;
  }
  return <StandaloneApp />;
}

// ---------------------------------------------------------------------------
// Standalone mode
// ---------------------------------------------------------------------------

function StandaloneApp(): JSX.Element {
  const session = useSession();
  const [messages, setMessages] = useState<readonly ChatMessage[]>([]);
  const conversationIdRef = useRef<string>(makeConversationId());

  if (session.state === 'loading') {
    return (
      <div className="login-page">
        <div className="login-page__card">
          <p>Loading...</p>
        </div>
      </div>
    );
  }

  if (session.state === 'unauthenticated') {
    return <LoginPage />;
  }

  // Empty state — show the CareTeam panel until the conversation has its
  // first turn. Click-to-brief wiring is issue 005; for now the panel is
  // visible above the chat composer so the user can also just start typing.
  return (
    <AppShell user={session.user}>
      {messages.length === 0 ? <PanelView /> : null}
      <AgentPanel
        open={true}
        surface="panel"
        density="regular"
        showCitations={true}
        accent="#4abfac"
        conversationId={conversationIdRef.current}
        patientId=""
        userId=""
        smartAccessToken=""
        patientName={DEFAULT_PATIENT_NAME}
        messages={messages}
        setMessages={(updater) => setMessages((prev) => updater(prev))}
        onClose={() => {}}
        onCite={() => {}}
      />
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// EHR-launch mode (original behavior, preserved)
// ---------------------------------------------------------------------------

function EhrLaunchApp({ smart }: { readonly smart: SmartLaunchContext }): JSX.Element {
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [open, setOpen] = useState<boolean>(true);
  const [messages, setMessages] = useState<readonly ChatMessage[]>([]);
  const [highlights, setHighlights] = useState<Record<string, boolean>>({});

  const conversationIdRef = useRef<string>(smart.conversationId ?? makeConversationId());

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
    setHighlights({ [citation.card]: true });
    window.setTimeout(() => setHighlights({}), 1700);

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
      // ignore - non-iframe context
    }

    const el = document.querySelector(`[data-card="${citation.card}"]`);
    if (el instanceof HTMLElement) {
      const r = el.getBoundingClientRect();
      if (r.top < 80 || r.bottom > window.innerHeight - 20) {
        window.scrollTo({ top: window.scrollY + r.top - 120, behavior: 'smooth' });
      }
    }
  }

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
