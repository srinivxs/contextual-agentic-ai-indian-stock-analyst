import { render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MatchView } from '@/components/MatchView';
import type { MatchStatus } from '@/lib/match';
import {
  demoCitation,
  demoMatches,
  demoReason,
  demoStockMatch,
  installFakeApi,
} from '../helpers/fakeApi';

const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(''),
}));

beforeEach(() => nav.replace.mockReset());

const profiled = (...stocks: ReturnType<typeof demoStockMatch>[]) =>
  demoMatches({ profile_empty: false, stocks });

describe('the match page', () => {
  it('marks Match as the current menu item', async () => {
    installFakeApi({ matches: demoMatches() });
    render(<MatchView />);
    const main = await screen.findByRole('navigation', { name: 'Main' });
    expect(within(main).getByRole('link', { name: 'Match' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('marks a must-have and states each outcome in words, not by colour alone', async () => {
    installFakeApi({ matches: profiled(demoStockMatch()) });
    render(<MatchView />);
    const card = await screen.findByRole('region', { name: 'DemoCo Alpha Limited' });
    expect(within(card).getAllByRole('listitem')[0]).toHaveTextContent('must-have');
    expect(within(card).getByText('DE', { selector: '.monogram' })).toBeInTheDocument();
  });

  it('asks an empty profile to tell the chat first, with a link to the chat', async () => {
    installFakeApi({ matches: demoMatches() });
    render(<MatchView />);

    expect(
      await screen.findByRole('heading', { level: 1, name: 'How the stocks fit your profile' }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        /Tell the chat your preferences first, for example: I'm conservative/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the chat' })).toHaveAttribute('href', '/chat/');
    expect(screen.getByText(/share prices are not used/)).toBeInTheDocument();
  });

  it.each<[MatchStatus, string]>([
    ['match', 'Match'],
    ['partial', 'Partial match'],
    ['no_match', 'No match'],
    ['not_enough_data', 'Not enough data'],
  ])('shows the %s status in words', async (status, label) => {
    installFakeApi({ matches: profiled(demoStockMatch({ status })) });
    render(<MatchView />);

    const card = await screen.findByRole('region', { name: 'DemoCo Alpha Limited' });
    expect(within(card).getByText(label, { selector: '.match-badge' })).toBeInTheDocument();
  });

  it('lists a card per stock, its reasons with outcome words, and must-have marks', async () => {
    installFakeApi({
      matches: profiled(
        demoStockMatch({
          reasons: [
            demoReason(),
            demoReason({
              criterion: 'dividend',
              preference: 'income',
              hard: false,
              outcome: 'miss',
              text: 'The company pays no dividend.',
              citations: [],
            }),
            demoReason({ criterion: 'quality', hard: false, outcome: 'fail', text: 'Q.' }),
            // a stock-specific "can't be judged" (debt for a bank) stays in its card
            demoReason({
              criterion: 'debt',
              preference: 'conservative',
              hard: false,
              outcome: 'not_assessable',
              text: 'V.',
            }),
            demoReason({ criterion: 'momentum', hard: false, outcome: 'no_data', text: 'M.' }),
          ],
        }),
        demoStockMatch({ symbol: 'DEMOB', name: 'DemoCo Beta Limited', status: 'no_match' }),
      ),
    });
    render(<MatchView />);

    const card = await screen.findByRole('region', { name: 'DemoCo Alpha Limited' });
    expect(screen.getByRole('region', { name: 'DemoCo Beta Limited' })).toBeInTheDocument();
    const items = within(card).getAllByRole('listitem');
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent('Meets');
    expect(items[0]).toHaveTextContent('must-have');
    expect(items[0]).toHaveTextContent('Debt to equity is 0.25');
    expect(items[1]).toHaveTextContent('Falls short');
    expect(items[1]).not.toHaveTextContent('must-have');
    expect(items[2]).toHaveTextContent('Fails a must-have');
    expect(items[3]).toHaveTextContent("Can't be judged");
    expect(items[4]).toHaveTextContent('No data');
  });

  it('links a citation chip to the filing when the address is allowed', async () => {
    installFakeApi({ matches: profiled(demoStockMatch()) });
    render(<MatchView />);

    const chip = await screen.findByRole('link', { name: demoCitation().label });
    expect(chip).toHaveAttribute('href', demoCitation().url);
    expect(chip).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('shows a citation whose address is not allowed as plain text', async () => {
    const bad = demoCitation({ label: 'Odd source', url: 'javascript:alert(1)' });
    installFakeApi({
      matches: profiled(demoStockMatch({ reasons: [demoReason({ citations: [bad] })] })),
    });
    render(<MatchView />);

    expect(await screen.findByText('Odd source')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Odd source' })).not.toBeInTheDocument();
  });

  it('shows cautions apart from the reasons', async () => {
    installFakeApi({
      matches: profiled(
        demoStockMatch({
          cautions: [
            demoReason({
              criterion: 'sentiment',
              hard: false,
              outcome: 'miss',
              text: 'News sentiment is negative.',
              citations: [],
            }),
          ],
        }),
      ),
    });
    render(<MatchView />);

    const card = await screen.findByRole('region', { name: 'DemoCo Alpha Limited' });
    const caution = within(card).getByText('News sentiment is negative.').closest('li');
    expect(caution).toHaveTextContent('Caution');
    expect(caution?.closest('ul')).toHaveClass('match-cautions');
  });

  it('puts the disclaimer at the bottom', async () => {
    installFakeApi({ matches: profiled(demoStockMatch()) });
    render(<MatchView />);
    await screen.findByRole('region', { name: 'DemoCo Alpha Limited' });
    expect(screen.getByText(/share prices are not used/)).toBeInTheDocument();
  });

  it('sends a signed-out visitor to the sign-in page without asking for matches', async () => {
    const api = installFakeApi({ signedIn: false });
    render(<MatchView />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(api.requests).not.toContain('GET /api/v1/match');
  });

  it('goes back to the sign-in page when the session has ended', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/match', 401);
    render(<MatchView />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('shows an error, not the sign-in page, when the match cannot be loaded', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/match', 500);
    render(<MatchView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t load/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('shows an error when it cannot find out who is signed in', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/me', 500);
    render(<MatchView />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t load/i);
  });

  it('shows preferences no stock can be judged on once, above the cards', async () => {
    const horizon = demoReason({
      criterion: 'horizon',
      preference: 'short_term',
      hard: false,
      outcome: 'not_assessable',
      text: 'The app has no price history, so a horizon does not change the result.',
      citations: [],
    });
    installFakeApi({
      matches: profiled(
        demoStockMatch({ reasons: [demoReason(), horizon] }),
        demoStockMatch({ symbol: 'DEMOB', name: 'DemoCo Beta', reasons: [demoReason(), horizon] }),
      ),
    });
    render(<MatchView />);
    const note = await screen.findByRole('note', { name: 'Not judged for any stock' });
    expect(note).toHaveTextContent(/no price history/);
    expect(screen.getAllByText(/no price history/)).toHaveLength(1);
    const alpha = screen.getByRole('region', { name: 'DemoCo Alpha Limited' });
    expect(within(alpha).queryByText(/no price history/)).toBeNull();
    expect(within(alpha).getByText(/within the 1.0 limit/)).toBeInTheDocument();
  });

  it('says plainly when nothing in the profile can be checked, instead of empty cards', async () => {
    const value = demoReason({
      criterion: 'value',
      preference: 'value',
      hard: false,
      outcome: 'not_assessable',
      text: 'Value needs share prices, which this app does not have.',
      citations: [],
    });
    installFakeApi({
      matches: profiled(
        demoStockMatch({ status: 'not_enough_data', reasons: [value] }),
        demoStockMatch({
          symbol: 'DEMOB',
          name: 'DemoCo Beta',
          status: 'not_enough_data',
          reasons: [value],
        }),
      ),
    });
    render(<MatchView />);
    expect(
      await screen.findByText(
        /Nothing in your profile can be checked against the stored figures and prices yet/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'DemoCo Alpha Limited' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Open the chat' })).toHaveAttribute('href', '/chat/');
    expect(screen.getByRole('note', { name: 'Not judged for any stock' })).toHaveTextContent(
      /Value needs share prices/,
    );
  });

  it('keeps a judged value reason, or one judged for only some stocks, in its card', async () => {
    const judged = demoReason({
      criterion: 'value',
      preference: 'value',
      hard: false,
      outcome: 'pass',
      text: 'Price to earnings is 12.5, at or below 20.',
    });
    const oneOnly = demoReason({
      criterion: 'value',
      preference: 'value',
      hard: false,
      outcome: 'not_assessable',
      text: 'A bonus since the EPS year: per-share figures no longer compare.',
      citations: [],
    });
    installFakeApi({
      matches: profiled(
        demoStockMatch({ reasons: [demoReason(), judged] }),
        demoStockMatch({ symbol: 'DEMOB', name: 'DemoCo Beta', reasons: [demoReason(), oneOnly] }),
      ),
    });
    render(<MatchView />);
    const alpha = await screen.findByRole('region', { name: 'DemoCo Alpha Limited' });
    expect(within(alpha).getByText(/Price to earnings is 12.5/)).toBeInTheDocument();
    const beta = screen.getByRole('region', { name: 'DemoCo Beta' });
    expect(within(beta).getByText(/A bonus since the EPS year/)).toBeInTheDocument();
    expect(screen.queryByRole('note', { name: 'Not judged for any stock' })).toBeNull();
  });
});
