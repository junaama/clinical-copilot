/**
 * Conversation sidebar (issue 004).
 *
 * Renders the user's threads, ordered most-recently-touched first. The "+"
 * button mints a new conversation via POST /conversations and signals the
 * parent to navigate to it. Each row is a clickable link that opens the
 * thread by id.
 *
 * Title source today is the truncated first user message; issue 008 will
 * swap this for a Haiku summary on a separate write-behind pass.
 */

import { useEffect, useState, type JSX } from 'react';
import {
  createConversation,
  fetchConversations,
  type ConversationRow,
} from '../api/conversations';
import type { PanelPatient } from '../api/panel';
import { PanelView } from './PanelView';

export interface ConversationSidebarProps {
  /** Currently active conversation; null = fresh-thread / panel mode. */
  readonly activeConversationId: string | null;
  /** Bumped by the parent after each chat turn so the list refetches. */
  readonly refreshToken: number;
  /** Caller navigates the URL when this fires. */
  readonly onSelect: (conversationId: string) => void;
  /** Caller navigates the URL when this fires. */
  readonly onCreate: (conversationId: string) => void;
  /** Caller focuses a patient from the care-team roster. */
  readonly onPatientClick?: (patient: PanelPatient) => void;
}

type SidebarState =
  | { readonly state: 'loading' }
  | { readonly state: 'loaded'; readonly rows: readonly ConversationRow[] }
  | { readonly state: 'error' };

export function ConversationSidebar(
  props: ConversationSidebarProps,
): JSX.Element {
  const {
    activeConversationId,
    refreshToken,
    onSelect,
    onCreate,
    onPatientClick,
  } = props;
  const [data, setData] = useState<SidebarState>({ state: 'loading' });
  const [creating, setCreating] = useState<boolean>(false);
  const [careTeamOpen, setCareTeamOpen] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    fetchConversations().then((resp) => {
      if (cancelled) return;
      if (resp === null) {
        setData({ state: 'error' });
      } else {
        setData({ state: 'loaded', rows: resp.conversations });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  async function handleCreate(): Promise<void> {
    if (creating) return;
    setCreating(true);
    const created = await createConversation();
    setCreating(false);
    if (created !== null) {
      onCreate(created.id);
    }
  }

  return (
    <aside className="conv-sidebar" aria-label="Care team and conversation history">
      <section className="conv-sidebar__care">
        <button
          type="button"
          className="conv-sidebar__section-toggle"
          aria-expanded={careTeamOpen}
          aria-controls="conv-sidebar-care-team"
          onClick={() => setCareTeamOpen((open) => !open)}
        >
          <span>Care team</span>
          <span className="conv-sidebar__chevron" aria-hidden="true">
            {careTeamOpen ? '-' : '+'}
          </span>
        </button>
        <div
          id="conv-sidebar-care-team"
          className="conv-sidebar__care-body"
          hidden={!careTeamOpen}
        >
          <PanelView onPatientClick={onPatientClick} />
        </div>
      </section>

      <div className="conv-sidebar__header">
        <h2 className="conv-sidebar__title">Conversations</h2>
        <button
          type="button"
          className="conv-sidebar__new-btn"
          onClick={() => void handleCreate()}
          disabled={creating}
          aria-label="New conversation"
          title="New conversation"
        >
          +
        </button>
      </div>

      <div className="conv-sidebar__history" aria-label="Conversation history">
        {data.state === 'loading' ? (
          <p className="conv-sidebar__loading">Loading…</p>
        ) : null}

        {data.state === 'error' ? (
          <p className="conv-sidebar__error">Couldn’t load conversations.</p>
        ) : null}

        {data.state === 'loaded' && data.rows.length === 0 ? (
          <p className="conv-sidebar__empty">No conversations yet.</p>
        ) : null}

        {data.state === 'loaded' && data.rows.length > 0 ? (
          <ul className="conv-sidebar__list">
            {data.rows.map((r) => {
              const isActive = r.id === activeConversationId;
              return (
                <li key={r.id} className="conv-sidebar__row">
                  <button
                    type="button"
                    className={
                      isActive
                        ? 'conv-sidebar__row-btn conv-sidebar__row-btn--active'
                        : 'conv-sidebar__row-btn'
                    }
                    onClick={() => onSelect(r.id)}
                    aria-current={isActive ? 'true' : undefined}
                  >
                    <div className="conv-sidebar__row-title">
                      {r.title || '(untitled)'}
                    </div>
                    {r.last_focus_pid ? (
                      <div className="conv-sidebar__row-meta">
                        Patient {r.last_focus_pid}
                      </div>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </aside>
  );
}
