/**
 * The conversational agent panel.
 *
 * Owns the chat transcript, the input box, and the wire calls to /chat. The
 * parent App owns toggle state, SMART context, and Tweaks values.
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type JSX, type ReactNode } from 'react';
import { sendChat } from '../api/client';
import type { ChatResponse, Citation } from '../api/types';
import { deriveAgentContext } from '../lib/agentContext';
import { cleanSyntheticNameSuffixes } from '../lib/displayName';
import { AgentMsg, AgentErrorBubble, type AgentMessage } from './AgentMsg';
import { Launcher } from './Launcher';
import { Thinking } from './Thinking';
import { UserMsg } from './UserMsg';
import { Welcome } from './Welcome';

export type Surface = 'panel' | 'floating' | 'inline';
export type Density = 'compact' | 'regular' | 'comfy';

// Time after the response lands before we flip `streaming` false. The
// typewriter runs at ~3 chars/14ms; a 1.4s tail covers a 300-char lead.
const STREAM_TAIL_MS = 1400;

export interface ChatMessage {
  readonly id: string;
  readonly role: 'user' | 'agent' | 'agent-error';
  readonly text?: string;
  readonly auto?: boolean;
  readonly agent?: AgentMessage;
  readonly error?: { readonly status: number; readonly detail: string };
}

/**
 * Sentinel a parent uses to inject a synthetic user message — the click-to-brief
 * wire (issue 005). The `id` must change with each new injection so AgentPanel
 * fires `ask` once per click; identical ids are ignored (re-renders no-op).
 */
export interface PendingUserMessage {
  readonly id: string;
  readonly text: string;
}

export interface AgentPanelProps {
  readonly open: boolean;
  readonly surface: Surface;
  readonly density: Density;
  readonly showCitations: boolean;
  readonly accent: string;
  readonly conversationId: string;
  readonly patientId: string;
  readonly userId: string;
  readonly smartAccessToken: string;
  readonly patientName: string;
  /** Issue 043: id of the currently focused patient (SMART context, panel
   *  click, or backend-resolved). Empty string means no patient yet. The
   *  agent context module reads this to decide whether the no-patient,
   *  panel-capable, or patient-focused gating applies. */
  readonly focusPatientId: string;
  /** Issue 043: true when the surrounding shell mounts a panel surface
   *  (the standalone app's care-team panel). The EHR-launch shell does
   *  not, so panel-wide prompts are disabled there until a patient is
   *  resolved. */
  readonly hasPanelSurface: boolean;
  readonly messages: readonly ChatMessage[];
  readonly setMessages: (
    update: (prev: readonly ChatMessage[]) => readonly ChatMessage[],
  ) => void;
  readonly onClose: () => void;
  readonly onCite: (citation: Citation) => void;
  readonly pendingUserMessage?: PendingUserMessage | null;
  readonly onPendingMessageHandled?: () => void;
  /** Fires once per successful /chat response so the shell can react to
   *  patient-focus changes (issue 011: upload widget visibility). */
  readonly onResponse?: (response: ChatResponse) => void;
  /** Optional element rendered just above the chat composer — used by the
   *  shell to embed the document-upload affordance inside the chat window. */
  readonly composerSlot?: ReactNode;
  /** Optional evidence/result surface rendered inside the chat scroll. */
  readonly evidenceSlot?: ReactNode;
}

export function AgentPanel(props: AgentPanelProps): JSX.Element | null {
  const {
    open,
    surface,
    density,
    showCitations,
    accent,
    conversationId,
    patientId,
    userId,
    smartAccessToken,
    patientName,
    focusPatientId,
    hasPanelSurface,
    messages,
    setMessages,
    onClose,
    onCite,
    pendingUserMessage,
    onPendingMessageHandled,
    onResponse,
    composerSlot,
    evidenceSlot,
  } = props;

  const [draft, setDraft] = useState<string>('');
  const [busy, setBusy] = useState<boolean>(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const idCounter = useRef<number>(0);
  const lastPendingIdRef = useRef<string | null>(null);

  function nextId(): string {
    idCounter.current += 1;
    return `m-${Date.now()}-${idCounter.current}`;
  }

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, busy]);

  async function ask(message: string, opts: { auto?: boolean } = {}): Promise<void> {
    const trimmed = message.trim();
    if (trimmed.length === 0 || busy) return;

    const userMsg: ChatMessage = {
      id: nextId(),
      role: 'user',
      text: trimmed,
      auto: opts.auto === true,
    };
    setMessages((prev) => [...prev, userMsg]);
    setBusy(true);

    const result = await sendChat({
      request: {
        conversation_id: conversationId,
        patient_id: patientId,
        user_id: userId,
        message: trimmed,
        smart_access_token: smartAccessToken,
      },
    });

    if (!result.ok) {
      const errMsg: ChatMessage = {
        id: nextId(),
        role: 'agent-error',
        error: { status: result.status, detail: result.detail },
      };
      setMessages((prev) => [...prev, errMsg]);
      setBusy(false);
      return;
    }

    const agentId = nextId();
    const agentMsg: ChatMessage = {
      id: agentId,
      role: 'agent',
      agent: {
        role: 'agent',
        block: result.response.block,
        streaming: true,
        route: result.response.state.route,
        // Issue 042: per-turn diagnostics for the Technical details
        // affordance. The data carried here never bleeds into the
        // clinical answer — AgentMsg renders it inside a collapsed
        // ``<details>`` so the clinician sees only the lead unless
        // they open it.
        debugInfo: {
          route: result.response.state.route,
          workflow_id: result.response.state.workflow_id,
          classifier_confidence: result.response.state.classifier_confidence,
          diagnostics: result.response.state.diagnostics,
        },
      },
    };
    setMessages((prev) => [...prev, agentMsg]);
    setBusy(false);
    onResponse?.(result.response);

    // Flip streaming false after the typewriter has a chance to finish so the
    // body (cohort/deltas/timeline/citations/followups) paints in.
    window.setTimeout(() => {
      setMessages((prev) =>
        prev.map((m): ChatMessage => {
          if (m.id !== agentId || m.agent === undefined) return m;
          return { ...m, agent: { ...m.agent, streaming: false } };
        }),
      );
    }, STREAM_TAIL_MS);
  }

  function submit(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    void ask(draft);
    setDraft('');
  }

  // Synthetic-message channel — see issue 005. The parent sets
  // `pendingUserMessage` with a fresh `id` to enqueue a single ask; the
  // ref-tracked id-dedupe makes re-renders idempotent.
  useEffect(() => {
    if (!pendingUserMessage) return;
    if (pendingUserMessage.id === lastPendingIdRef.current) return;
    lastPendingIdRef.current = pendingUserMessage.id;
    void ask(pendingUserMessage.text);
    onPendingMessageHandled?.();
    // ask & onPendingMessageHandled are stable enough for this slice; the
    // dedupe ref guarantees a single fire per id regardless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingUserMessage]);

  function handleJumpToVitals(): void {
    onCite({ card: 'vitals', label: 'Vitals', fhir_ref: null });
  }

  // Issue 043: single decision module for no-patient / patient-focused /
  // panel-capable gating. Drives the welcome copy, suggestion chip
  // enablement, composer placeholder, and Send button hint so the four
  // surfaces stay in sync (story 34).
  const agentContext = useMemo(
    () =>
      deriveAgentContext({
        focusPatientId,
        hasPanelSurface,
        focusPatientName: patientName,
      }),
    [focusPatientId, hasPanelSurface, patientName],
  );

  // Issue 039: header subtitle reflects the latest route metadata so a
  // panel / guideline / document / refusal answer is not mislabeled as
  // "Reading this patient's record". Falls back to a context-aware copy
  // (chart record vs panel-only) until the first agent answer carries
  // a route — issue 043 made the no-patient subtitle no longer say
  // "Reading this patient's record".
  const latestRouteLabel = useMemo<string | null>(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m?.role === 'agent' && m.agent?.route) {
        return m.agent.route.label;
      }
    }
    return null;
  }, [messages]);
  const fallbackSubtitleLabel =
    agentContext.kind === 'patient-focused'
      ? `Reading ${cleanSyntheticNameSuffixes(patientName)}'s record`
      : agentContext.kind === 'panel-capable'
        ? 'Reviewing your panel'
        : 'No patient selected';
  const subtitle =
    latestRouteLabel !== null
      ? `${latestRouteLabel} · FHIR R4`
      : `${fallbackSubtitleLabel} · FHIR R4`;

  if (!open) return null;

  const widthClass =
    surface === 'panel'
      ? 'agent-panel-side'
      : surface === 'floating'
        ? 'agent-panel-floating'
        : 'agent-panel-inline';

  // CSS variables aren't part of CSSProperties' index signature; cast a record.
  const accentStyle = { '--accent': accent } as Record<string, string> as CSSProperties;

  return (
    <aside
      className={`agent-panel ${widthClass}`}
      data-density={density}
      style={accentStyle}
    >
      <header className="agent-hd">
        <div className="agent-hd-l">
          <span className="agent-mark" aria-hidden="true">
            <span className="agent-mark-dot" />
          </span>
          <div>
            <div className="agent-title">Chart Agent</div>
            <div className="agent-sub" data-testid="agent-subtitle">{subtitle}</div>
          </div>
        </div>
        <div className="agent-hd-r">
          <button
            className="agent-icon close"
            title="Close"
            aria-label="close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
      </header>

      <div className="agent-scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <Welcome context={agentContext} onPick={(label) => void ask(label)} />
        )}
        {messages.map((m) => {
          if (m.role === 'user' && m.text !== undefined) {
            return <UserMsg key={m.id} text={m.text} auto={m.auto === true} />;
          }
          if (m.role === 'agent' && m.agent !== undefined) {
            return (
              <AgentMsg
                key={m.id}
                message={m.agent}
                showCitations={showCitations}
                onCite={onCite}
                onFollowup={(label) => void ask(label)}
                onJumpToVitals={handleJumpToVitals}
              />
            );
          }
          if (m.role === 'agent-error' && m.error !== undefined) {
            return (
              <AgentErrorBubble
                key={m.id}
                status={m.error.status}
                detail={m.error.detail}
              />
            );
          }
          return null;
        })}
        {busy && <Thinking />}
        {evidenceSlot ? (
          <div className="agent-evidence-slot">{evidenceSlot}</div>
        ) : null}
      </div>

      {composerSlot ? (
        <div className="agent-composer-slot">{composerSlot}</div>
      ) : null}

      <form className="agent-input" onSubmit={submit} data-context-kind={agentContext.kind}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={agentContext.composerPlaceholder}
          aria-label="Ask the chart agent"
          data-testid="agent-input"
        />
        <button
          type="submit"
          aria-label="Send"
          disabled={draft.trim().length === 0}
          title={
            draft.trim().length === 0
              ? agentContext.sendDisabledHint
              : 'Send message'
          }
          data-testid="agent-send"
        >
          ↵
        </button>
      </form>
      {draft.trim().length === 0 && (
        <div
          className="agent-input-hint"
          data-testid="agent-send-hint"
          aria-live="polite"
        >
          {agentContext.sendDisabledHint}
        </div>
      )}
      <div className="agent-foot">
        <span>⌘K to summon · answers cite the chart · read-only</span>
      </div>
    </aside>
  );
}

export { Launcher };
