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
  it('exists and has the two static routes', () => {
    expect(existsSync(SRC)).toBe(true);
    const pages = allPaths.map(posix);
    expect(pages).toContain('src/app/page.tsx');
    expect(pages).toContain('src/app/stocks/page.tsx');
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
    ['hard-coded absolute URLs', /https?:\/\//],
  ])('never uses %s', (_label, pattern) => {
    expect(sourceFiles.length).toBeGreaterThan(0); // nothing to scan must not count as clean
    const offenders = sourceFiles.filter((file) => pattern.test(read(file))).map(posix);
    expect(offenders).toEqual([]);
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
