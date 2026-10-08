import { render, screen } from '@testing-library/react';
import type { ComponentType } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ChatView } from '@/components/ChatView';
import { DocumentsView } from '@/components/DocumentsView';
import { HomeView } from '@/components/HomeView';
import { MatchView } from '@/components/MatchView';
import { NewsView } from '@/components/NewsView';
import { SignInView } from '@/components/SignInView';
import { StocksView } from '@/components/StocksView';
import { StockView } from '@/components/StockView';

import { installFakeApi } from '../helpers/fakeApi';

/*
 * The AWS stack is switched off between sessions on purpose (the owner, 2026-10-09). The static
 * site still loads from CloudFront, but every /api call gets a 502 (no load balancer behind it).
 * A visitor should then read that the demo is off on purpose, not a broken page or an error.
 */

const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams('symbol=TCS'),
}));

const PAGES: [string, ComponentType][] = [
  ['the sign-in page', SignInView],
  ['Home', HomeView],
  ['Chat', ChatView],
  ['Stocks', StocksView],
  ['a stock page', StockView],
  ['News', NewsView],
  ['Documents', DocumentsView],
  ['Match', MatchView],
];

describe('when the demo is switched off', () => {
  it.each(PAGES)('%s says it is off on purpose and whom to ask', async (_name, Page) => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/me', 502); // CloudFront: nothing behind /api
    render(<Page />);

    expect(
      await screen.findByRole('heading', { name: 'The live demo is switched off' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/switches it off between sessions on purpose/)).toBeInTheDocument();
    expect(screen.getByText(/Ask the person who shared this link/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull(); // not an error
    expect(screen.queryByRole('link', { name: 'Sign in with Google' })).toBeNull();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('a real server fault is still reported as one, not as switched off', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/me', 500);
    render(<SignInView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/something went wrong/i);
    expect(screen.queryByRole('heading', { name: 'The live demo is switched off' })).toBeNull();
  });
});
