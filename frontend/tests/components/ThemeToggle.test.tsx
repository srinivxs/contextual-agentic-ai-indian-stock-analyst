import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ThemeToggle } from '@/components/ThemeToggle';
import { THEME_KEY, THEME_SCRIPT, currentTheme, setTheme } from '@/lib/theme';

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

afterEach(() => vi.unstubAllGlobals());

describe('the theme', () => {
  it('reads the mode from the page, light unless it says dark', () => {
    expect(currentTheme()).toBe('light');
    document.documentElement.dataset.theme = 'dark';
    expect(currentTheme()).toBe('dark');
  });

  it('sets the mode on the page and remembers it', () => {
    setTheme('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(localStorage.getItem(THEME_KEY)).toBe('dark');
  });

  it('still switches when the browser refuses storage', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    setTheme('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    vi.restoreAllMocks();
  });

  it('the head script uses the stored choice first', () => {
    localStorage.setItem(THEME_KEY, 'dark');
    new Function(THEME_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('the head script follows the system when nothing is stored', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('dark') }));
    new Function(THEME_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe('dark');
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    new Function(THEME_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('the head script falls back to light when anything fails', () => {
    vi.stubGlobal('matchMedia', () => {
      throw new Error('no media queries');
    });
    new Function(THEME_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('the dark mode switch', () => {
  it('is a labelled switch that flips the mode and its own state', async () => {
    render(<ThemeToggle />);
    const toggle = screen.getByRole('switch', { name: 'Dark mode' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('starts from the mode the head script chose', () => {
    document.documentElement.dataset.theme = 'dark';
    render(<ThemeToggle />);
    expect(screen.getByRole('switch', { name: 'Dark mode' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('follows a change made elsewhere on the page', async () => {
    render(<ThemeToggle />);
    const toggle = screen.getByRole('switch', { name: 'Dark mode' });
    await act(async () => {
      document.documentElement.dataset.theme = 'dark';
      await Promise.resolve();
    });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });
});
