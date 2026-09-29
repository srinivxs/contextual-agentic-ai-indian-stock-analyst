/**
 * A stock's mark in a circle: the company's logo for the three stocks the app follows (supplied by
 * the owner, served from public/logos/, used only to identify the company next to its name), and
 * two letters in a neutral tint for anything else (the RBI mark, the fictional stocks in tests).
 */
const TINTS = ['tint-a', 'tint-b', 'tint-c', 'tint-d'] as const;

const LOGOS: Record<string, string> = {
  RELIANCE: '/logos/RELIANCE.png',
  TCS: '/logos/TCS.png',
  HDFCBANK: '/logos/HDFCBANK.png',
};

/**
 * Ask the browser for every logo now, so a stock picked later shows its logo at once instead of a
 * blank circle while the file downloads (seen when switching stocks on the chat page).
 */
export function preloadLogos(): void {
  for (const src of Object.values(LOGOS)) {
    const image = new Image();
    image.src = src;
  }
}

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
  const logo = LOGOS[symbol];
  if (logo !== undefined) {
    return (
      <span className={`monogram ${size} has-logo`} aria-hidden="true">
        {/* A plain img: the export is static, and the three files are tiny (96 px). */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={logo} alt="" width={96} height={96} />
      </span>
    );
  }
  return (
    <span className={`monogram ${size} ${monogramTint(symbol)}`} aria-hidden="true">
      {monogramLetters(symbol)}
    </span>
  );
}
