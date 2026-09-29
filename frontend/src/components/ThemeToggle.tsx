'use client';

import { useSyncExternalStore } from 'react';

import { MoonIcon, SunIcon } from '@/components/Icons';
import { currentTheme, onThemeChange, setTheme } from '@/lib/theme';

/** The dark mode switch in the top bar. The page's own attribute is the one source of truth. */
export function ThemeToggle() {
  const theme = useSyncExternalStore(onThemeChange, currentTheme, () => 'light');
  const dark = theme === 'dark';
  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label="Dark mode"
      title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="theme-switch"
      onClick={() => setTheme(dark ? 'light' : 'dark')}
    >
      <span className="theme-switch-track" aria-hidden="true">
        <span className="theme-switch-thumb">{dark ? <MoonIcon /> : <SunIcon />}</span>
      </span>
    </button>
  );
}
