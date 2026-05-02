/**
 * Empty-state CareTeam roster (issue 002).
 *
 * Mounted as the body of AppShell when the active conversation has no turns
 * yet. Each row shows enough detail for a clinician to recognize the patient
 * (name, DOB, last admission) so they can decide who to ask about first.
 *
 * Click handling is intentionally non-functional in this slice — wiring a
 * click to "inject 'give me a brief on X' as a synthetic user message" is
 * issue 005 (click-to-brief). The button surface is rendered now so the
 * affordance is visible.
 */

import { useEffect, useState, type JSX } from 'react';
import { fetchPanel, type PanelPatient, type PanelResponse } from '../api/panel';

interface PanelViewProps {
  readonly onPatientClick?: (patient: PanelPatient) => void;
}

type PanelState =
  | { readonly state: 'loading' }
  | { readonly state: 'loaded'; readonly data: PanelResponse }
  | { readonly state: 'error' };

export function PanelView({ onPatientClick }: PanelViewProps): JSX.Element {
  const [panel, setPanel] = useState<PanelState>({ state: 'loading' });

  useEffect(() => {
    let cancelled = false;
    fetchPanel().then((data) => {
      if (cancelled) return;
      setPanel(data === null ? { state: 'error' } : { state: 'loaded', data });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (panel.state === 'loading') {
    return (
      <div className="panel-view panel-view--loading">
        <p>Loading your patient panel…</p>
      </div>
    );
  }

  if (panel.state === 'error') {
    return (
      <div className="panel-view panel-view--error">
        <p>Couldn’t load your patient panel. Try refreshing.</p>
      </div>
    );
  }

  const patients = panel.data.patients;
  if (patients.length === 0) {
    return (
      <div className="panel-view panel-view--empty">
        <h2>No patients on your panel</h2>
        <p>
          You aren’t a member of any CareTeam yet. Ask an administrator to
          assign you, or start a conversation by typing a question below.
        </p>
      </div>
    );
  }

  return (
    <div className="panel-view">
      <h2 className="panel-view__title">Your patients</h2>
      <ul className="panel-view__list">
        {patients.map((p) => (
          <li key={p.patient_id} className="panel-view__row">
            <button
              type="button"
              className="panel-view__row-btn"
              onClick={() => onPatientClick?.(p)}
            >
              <div className="panel-view__name">
                {p.family_name}, {p.given_name}
              </div>
              <div className="panel-view__meta">
                <span>DOB {p.birth_date || '—'}</span>
                {p.last_admission ? (
                  <span>Last admit {p.last_admission.slice(0, 10)}</span>
                ) : null}
                {p.room ? <span>Room {p.room}</span> : null}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
