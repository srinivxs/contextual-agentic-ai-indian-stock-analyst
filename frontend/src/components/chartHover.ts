'use client';

import { useState, type MouseEvent } from 'react';

/**
 * Which point of a chart the mouse is over (the owner, 2026-10-08): null when it is not over the
 * chart. Points are spread evenly across the plot, so the nearest one is found from where the
 * mouse is along the width. Readers without a mouse keep the hidden table of every point.
 */
export function useChartHover(count: number) {
  const [hovered, setHovered] = useState<number | null>(null);
  const onMouseMove = (event: MouseEvent<HTMLElement>) => {
    if (count < 2) return; // one point: nothing to choose between
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width <= 0) return;
    const share = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    setHovered(Math.round(share * (count - 1)));
  };
  const onMouseLeave = () => setHovered(null);
  return { hovered, onMouseMove, onMouseLeave };
}

/** Where a point sits across the plot, in percent: 0 at the first point, 100 at the last. */
export function acrossPercent(index: number, count: number): number {
  return count > 1 ? (index / (count - 1)) * 100 : 100;
}

/** Which edge of the callout to pin to the point, so it never spills out of the chart. */
export function calloutSide(xPercent: number): 'side-start' | 'side-mid' | 'side-end' {
  if (xPercent >= 70) return 'side-end';
  if (xPercent <= 30) return 'side-start';
  return 'side-mid';
}
