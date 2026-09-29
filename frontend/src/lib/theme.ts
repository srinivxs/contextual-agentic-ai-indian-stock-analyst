/**
 * Light or dark mode. The mode lives on <html data-theme>, which the stylesheet reads; the choice
 * is remembered in localStorage. Until the reader chooses, the system's setting decides.
 */
export type Theme = 'light' | 'dark';

export const THEME_KEY = 'theme';

/**
 * Runs in the page head before anything is drawn (app/layout.tsx), so a dark-mode reader never
 * sees a white flash: the stored choice, else the system's, else light if anything fails.
 */
export const THEME_SCRIPT = `(function () {
  var root = document.documentElement;
  try {
    var stored = localStorage.getItem('${THEME_KEY}');
    root.dataset.theme =
      stored === 'light' || stored === 'dark'
        ? stored
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light';
  } catch (error) {
    root.dataset.theme = 'light';
  }
})();`;

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function setTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // storage blocked (a private window, say): the switch still works for this page
  }
}

/** Calls back whenever <html data-theme> changes, from this switch or anywhere else. */
export function onThemeChange(callback: () => void): () => void {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  return () => observer.disconnect();
}
