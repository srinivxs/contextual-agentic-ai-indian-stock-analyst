// @vitest-environment node
/**
 * Guards for the rules that are easy to break by accident and expensive to find later.
 *
 * ADR 006 (static export): no server rendering, route handlers, server actions, middleware or
 * dynamic ticker routes. ADR 012 (sessions): the browser never holds the session in script-visible
 * storage, and the frontend only ever talks to its own /api/v1.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { pathToFileURL } from 'node:url';

import { describe, expect, it } from 'vitest';

const ROOT = process.cwd();
const SRC = join(ROOT, 'src');

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? [path, ...walk(path)] : [path];
  });
}

const allPaths = existsSync(SRC) ? walk(SRC) : [];
const sourceFiles = allPaths.filter((p) => /\.(ts|tsx)$/.test(p));
const posix = (p: string): string => relative(ROOT, p).split('\\').join('/');

describe('the source tree', () => {
  it('exists and has the static routes', () => {
    expect(existsSync(SRC)).toBe(true);
    const pages = allPaths.map(posix);
    expect(pages).toContain('src/app/page.tsx');
    expect(pages).toContain('src/app/stocks/page.tsx');
    expect(pages).toContain('src/app/stock/page.tsx'); // one page for every stock, via ?symbol=
  });

  it('has no route handlers, middleware or proxy files (ADR 006)', () => {
    expect(allPaths.length).toBeGreaterThan(0); // an empty tree must not pass by accident
    const forbidden = [...allPaths, ...['middleware.ts', 'middleware.js', 'proxy.ts', 'proxy.js']
      .map((f) => join(ROOT, f))
      .filter(existsSync)].map(posix);
    expect(forbidden.filter((p) => /(^|\/)(route|middleware|proxy)\.(ts|tsx|js)$/.test(p))).toEqual([]);
    expect(forbidden.filter((p) => p.startsWith('src/app/api'))).toEqual([]);
  });

  it('has no dynamic route segments: arbitrary tickers use ?symbol=, not /stocks/[symbol]', () => {
    expect(allPaths.length).toBeGreaterThan(0);
    expect(allPaths.map(posix).filter((p) => p.includes('['))).toEqual([]);
  });
});

describe('the source code', () => {
  const read = (path: string): string => readFileSync(path, 'utf-8');

  it.each([
    ['server actions', /['"]use server['"]/],
    ['server-only APIs', /next\/headers|next\/server/],
    ['server-side data functions', /getServerSideProps|getStaticProps|generateStaticParams/],
    ['injected HTML', /dangerouslySetInnerHTML/],
    ['script-visible storage', /localStorage|sessionStorage|document\.cookie|indexedDB/],
    ['build-time public env vars', /NEXT_PUBLIC_/],
  ])('never uses %s', (_label, pattern) => {
    expect(sourceFiles.length).toBeGreaterThan(0); // nothing to scan must not count as clean
    const offenders = sourceFiles.filter((file) => pattern.test(read(file))).map(posix);
    expect(offenders).toEqual([]);
  });

  // The app only ever calls its own origin. The ONLY absolute URLs allowed in the source are two
  // link prefixes: official BSE filings (ADR 018) and screener.in company pages (ADR 020, the
  // source of some key facts). Both are links the user may click, never requests the app makes.
  // They are allowed in that one file, as those two exact lines, and nowhere else.
  const LINK_FILE = 'src/lib/documents.ts';
  const LINK_LINES = [
    "const OFFICIAL_PREFIX = 'https://www.bseindia.com/';",
    "const SCREENER_PREFIX = 'https://www.screener.in/company/';",
  ];

  const withoutAllowedLines = (text: string): string =>
    LINK_LINES.reduce((rest, line) => rest.replace(line, ''), text);

  it('never uses hard-coded absolute URLs, except the two source-link prefixes', () => {
    expect(sourceFiles.length).toBeGreaterThan(0);
    const offenders = sourceFiles
      .filter((file) => {
        const text = read(file);
        const scanned = posix(file).endsWith(LINK_FILE) ? withoutAllowedLines(text) : text;
        return /https?:\/\//.test(scanned);
      })
      .map(posix);
    expect(offenders).toEqual([]);
  });

  it('still has those exceptions where it says (so the allowance cannot go stale)', () => {
    const file = sourceFiles.find((path) => posix(path).endsWith(LINK_FILE));
    expect(file).toBeDefined();
    for (const line of LINK_LINES) expect(read(file ?? '')).toContain(line);
  });

  it('allows each exception once only: a second copy of an allowed line is still caught', () => {
    const doubled = `${LINK_LINES[0]}\n${LINK_LINES[0]}\n${LINK_LINES[1]}`;
    expect(/https?:\/\//.test(withoutAllowedLines(doubled))).toBe(true);
    expect(/https?:\/\//.test(withoutAllowedLines(LINK_LINES.join('\n')))).toBe(false);
  });
});

describe('next.config.mjs', () => {
  async function config(phase: string): Promise<Record<string, unknown>> {
    const loaded = (await import(pathToFileURL(join(ROOT, 'next.config.mjs')).href)) as {
      default: (phase: string) => Record<string, unknown>;
    };
    return loaded.default(phase);
  }

  it('builds a static export for production (ADR 006)', async () => {
    const built = await config('phase-production-build');

    expect(built.output).toBe('export');
    expect(built.trailingSlash).toBe(true);
    expect(built.images).toEqual({ unoptimized: true });
    // These features do not exist in a static export, so configuring them would be a silent lie.
    expect(built).not.toHaveProperty('rewrites');
    expect(built).not.toHaveProperty('redirects');
    expect(built).not.toHaveProperty('headers');
  });

  it('proxies /api to the backend in development only, so the browser stays on one origin', async () => {
    const dev = (await config('phase-development-server')) as {
      output?: unknown;
      skipTrailingSlashRedirect?: unknown;
      rewrites: () => Promise<{ source: string; destination: string }[]>;
    };

    expect(dev.output).toBeUndefined();
    // Without this, trailingSlash would redirect /api/v1/me to /api/v1/me/, which the backend lacks.
    expect(dev.skipTrailingSlashRedirect).toBe(true);
    const rules = await dev.rewrites();
    expect(rules).toEqual([
      { source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*' },
    ]);
  });

  it('takes the backend address from BACKEND_ORIGIN, never from the browser', async () => {
    process.env.BACKEND_ORIGIN = 'http://127.0.0.1:9100';
    try {
      const dev = (await config('phase-development-server')) as {
        rewrites: () => Promise<{ destination: string }[]>;
      };
      expect((await dev.rewrites())[0]?.destination).toBe('http://127.0.0.1:9100/api/:path*');
    } finally {
      delete process.env.BACKEND_ORIGIN;
    }
  });
});
