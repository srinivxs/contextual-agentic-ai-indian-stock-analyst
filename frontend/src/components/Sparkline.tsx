import type { Direction } from '@/lib/series';

/**
 * Where each value sits on a width x height canvas: left to right in order, the biggest at the top.
 * `pad` keeps the line off the edges. Fewer than two values draw nothing; equal values a flat line.
 */
export function linePoints(
  values: number[],
  width: number,
  height: number,
  pad: number,
): [number, number][] {
  if (values.length < 2) return [];
  const low = Math.min(...values);
  const span = Math.max(...values) - low;
  const usable = height - 2 * pad;
  return values.map((value, index) => [
    (index / (values.length - 1)) * width,
    span === 0 ? height / 2 : pad + usable * (1 - (value - low) / span),
  ]);
}

/** A tiny line beside a figure. Decorative: the figure next to it says the same in words. */
export function Sparkline({ values, tone }: { values: number[]; tone: Direction }) {
  const spots = linePoints(values, 80, 32, 3);
  return (
    <svg
      className={`home-spark ${tone}`}
      viewBox="0 0 80 32"
      width="80"
      height="32"
      aria-hidden="true"
      focusable="false"
    >
      {spots.length > 0 && (
        <polyline
          points={spots.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}
