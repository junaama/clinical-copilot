import type { JSX } from 'react';
import type { Trend } from '../api/types';

interface ScoreBarProps {
  readonly score: number;
  readonly trend: Trend;
}

const HIGH_THRESHOLD = 75;
const MED_THRESHOLD = 50;

function arrowFor(trend: Trend): string {
  if (trend === 'up') return '↑';
  if (trend === 'down') return '↓';
  return '→';
}

function classFor(score: number): 'high' | 'med' | 'low' {
  if (score >= HIGH_THRESHOLD) return 'high';
  if (score >= MED_THRESHOLD) return 'med';
  return 'low';
}

export function ScoreBar({ score, trend }: ScoreBarProps): JSX.Element {
  return (
    <span className={`agent-score ${classFor(score)}`}>
      <span className="agent-score-num">{score}</span>
      <span className="agent-score-arrow">{arrowFor(trend)}</span>
    </span>
  );
}
