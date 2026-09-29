/**
 * A stock's letters in a tinted circle: the app's stand-in for a logo (company logos are
 * trademarks and are not stored or shown). The tint depends only on the symbol, so a stock looks
 * the same on every page.
 */
const TINTS = ['tint-a', 'tint-b', 'tint-c', 'tint-d'] as const;

export function monogramLetters(symbol: string): string {
  return (
    symbol
      .replace(/[^A-Z0-9]/gi, '')
      .slice(0, 2)
      .toUpperCase() || '?'
  );
}

export function monogramTint(symbol: string): (typeof TINTS)[number] {
  let sum = 0;
  for (const char of symbol) sum += char.charCodeAt(0);
  return TINTS[sum % TINTS.length] ?? 'tint-a';
}

export function Monogram({ symbol, size = 'md' }: { symbol: string; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`monogram ${size} ${monogramTint(symbol)}`} aria-hidden="true">
      {monogramLetters(symbol)}
    </span>
  );
}
