/**
 * Citation-click side-effect plan.
 *
 * Different citation cards have different click behavior:
 *
 *  - Chart-card citations (vitals, labs, medications, problems, allergies,
 *    prescriptions, encounters, documents, other) flash the matching chart
 *    card in the host EHR via `copilot:flash-card` postMessage and scroll
 *    the local card into view.
 *
 *  - Guideline citations (issue 027) point at a RAG corpus chunk, not a
 *    chart card. Clicking the chip is a no-op for now — the source label
 *    is informative on its own. We deliberately do NOT postMessage with
 *    `card: 'guideline'`, because the chart container has no matching
 *    `[data-card="guideline"]` element and the host EHR has nothing to
 *    flash.
 *
 * `planCitationClick` returns a small description of what should happen
 * so the App-level effect handler can stay pure-stateful and the
 * decision logic can be unit-tested without rendering React.
 */
import type { Citation } from './types';

export interface ChartCardEffect {
  readonly kind: 'chart-card';
  readonly card: Citation['card'];
  readonly fhir_ref: string | null;
}

export interface NoOpEffect {
  readonly kind: 'noop';
  readonly reason: 'guideline';
}

export type CitationClickEffect = ChartCardEffect | NoOpEffect;

export function planCitationClick(citation: Citation): CitationClickEffect {
  if (citation.card === 'guideline') {
    return { kind: 'noop', reason: 'guideline' };
  }
  return {
    kind: 'chart-card',
    card: citation.card,
    fhir_ref: citation.fhir_ref,
  };
}
