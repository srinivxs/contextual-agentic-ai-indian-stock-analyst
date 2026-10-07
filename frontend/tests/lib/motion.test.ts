import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(__dirname, '../../src/app/globals.css'), 'utf-8');

describe('animations', () => {
  it('exist, and are switched off for anyone who asks the system for less motion', () => {
    expect(css).toMatch(/@keyframes\s+rise-in/);
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(reduced.length).toBeGreaterThan(0);
    expect(reduced).toMatch(/animation:\s*none\s*!important/);
    expect(reduced).toMatch(/transition:\s*none\s*!important/);
  });
});
