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

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import {
  AgentPanel,
  type ChatMessage,
  type Density,
  type PendingUserMessage,
  type Surface,
} from './components/AgentPanel';
import { AppShell } from './components/AppShell';
import { ConversationSidebar } from './components/ConversationSidebar';
import { Launcher } from './components/Launcher';
import { LoginPage } from './components/LoginPage';
import { PanelView } from './components/PanelView';
import type { PanelPatient } from './api/panel';
import { fetchConversationMessages } from './api/conversations';
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

const CONVERSATION_PATH_PREFIX = '/c/';

function readConversationIdFromUrl(): string | null {
  const path = window.location.pathname;
  if (!path.startsWith(CONVERSATION_PATH_PREFIX)) return null;
  const id = path.slice(CONVERSATION_PATH_PREFIX.length).replace(/\/+$/, '');
  return id.length > 0 ? id : null;
}

function navigateToConversation(conversationId: string): void {
  window.history.pushState({}, '', `${CONVERSATION_PATH_PREFIX}${conversationId}`);
}

function navigateToRoot(): void {
  window.history.pushState({}, '', '/');
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
  // Conversation id starts from the URL (deep-link-friendly: /c/<id> opens a
  // specific thread). When absent, we mint a fresh id so the chat can fire
  // even before the user creates an explicit thread via the sidebar's "+".
  const [conversationId, setConversationId] = useState<string>(
    () => readConversationIdFromUrl() ?? makeConversationId(),
  );
  // Tracks whether the URL-derived thread has been opened from the server
  // (messages rehydrated from the LangGraph checkpoint via
  // GET /conversations/:id/messages). Without this flag the sidebar would
  // miss prior turns when the user reloads on /c/<id>.
  const messageIdCounter = useRef<number>(0);
  const nextLocalMessageId = useCallback((): string => {
    messageIdCounter.current += 1;
    return `local-${Date.now()}-${messageIdCounter.current}`;
  }, []);

  const [pendingMessage, setPendingMessage] = useState<PendingUserMessage | null>(null);
  const [sidebarRefresh, setSidebarRefresh] = useState<number>(0);

  // Bump the sidebar refresh token whenever messages grow — the sidebar
  // refetches its list and the new conversation appears with its title.
  // Skips empty-state to avoid a needless first fetch on mount.
  useEffect(() => {
    if (messages.length === 0) return;
    setSidebarRefresh((n) => n + 1);
  }, [messages.length]);

  // Browser back/forward must update the active thread without a reload.
  useEffect(() => {
    function handlePop(): void {
      const fromUrl = readConversationIdFromUrl();
      if (fromUrl !== null) {
        setConversationId(fromUrl);
        setMessages([]);
      } else {
        // Root URL → fresh thread on the panel.
        setConversationId(makeConversationId());
        setMessages([]);
      }
    }
    window.addEventListener('popstate', handlePop);
    return () => window.removeEventListener('popstate', handlePop);
  }, []);

  // Rehydrate messages whenever the active conversation changes to one the
  // server already knows about (deep-link or sidebar-click). A fresh thread
  // (no server-side state yet) returns 404 / null and stays empty.
  useEffect(() => {
    if (session.state !== 'authenticated') return;
    let cancelled = false;
    fetchConversationMessages(conversationId).then((resp) => {
      if (cancelled) return;
      if (resp === null) return;
      const rehydrated: readonly ChatMessage[] = resp.messages.map((m) => {
        if (m.role === 'user') {
          return {
            id: nextLocalMessageId(),
            role: 'user',
            text: m.content,
          } satisfies ChatMessage;
        }
        return {
          id: nextLocalMessageId(),
          role: 'agent',
          agent: {
            role: 'agent',
            block: { kind: 'plain', lead: m.content },
            streaming: false,
          },
        } satisfies ChatMessage;
      });
      setMessages(rehydrated);
    });
    return () => {
      cancelled = true;
    };
  }, [conversationId, session.state, nextLocalMessageId]);

  // Click-to-brief (issue 005). Each click injects a synthetic
  //   "Give me a brief on <given> <family>."
  // user turn. If the current conversation already has turns we mint a
  // fresh thread first so the click-injected message lands as the first
  // turn of a new conversation rather than appending to an unrelated one.
  const handlePatientClick = useCallback(
    (patient: PanelPatient): void => {
      const text = `Give me a brief on ${patient.given_name} ${patient.family_name}.`;
      if (messages.length > 0) {
        const fresh = makeConversationId();
        setConversationId(fresh);
        setMessages([]);
        navigateToConversation(fresh);
      }
      setPendingMessage({
        id: `click-${patient.patient_id}-${Date.now().toString(36)}`,
        text,
      });
    },
    [messages.length],
  );

  // Sidebar callbacks. ``onSelect`` switches the active thread; ``onCreate``
  // navigates to the freshly-minted thread on the panel. Both push history
  // so the URL is shareable.
  const handleSelectConversation = useCallback(
    (id: string): void => {
      if (id === conversationId) return;
      setConversationId(id);
      setMessages([]);
      navigateToConversation(id);
    },
    [conversationId],
  );

  const handleCreateConversation = useCallback((id: string): void => {
    setConversationId(id);
    setMessages([]);
    navigateToConversation(id);
  }, []);

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

  return (
    <AppShell user={session.user}>
      <div className="standalone-body">
        <ConversationSidebar
          activeConversationId={conversationId}
          refreshToken={sidebarRefresh}
          onSelect={handleSelectConversation}
          onCreate={handleCreateConversation}
        />
        <div className="standalone-main">
          {messages.length === 0 && pendingMessage === null ? (
            <PanelView onPatientClick={handlePatientClick} />
          ) : null}
          <AgentPanel
            open={true}
            surface="panel"
            density="regular"
            showCitations={true}
            accent="#4abfac"
            conversationId={conversationId}
            patientId=""
            userId=""
            smartAccessToken=""
            patientName={DEFAULT_PATIENT_NAME}
            messages={messages}
            setMessages={(updater) => setMessages((prev) => updater(prev))}
            pendingUserMessage={pendingMessage}
            onPendingMessageHandled={() => setPendingMessage(null)}
            onClose={() => {
              navigateToRoot();
            }}
            onCite={() => {}}
          />
        </div>
      </div>
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
