import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { NewsView } from '@/components/NewsView';
import {
  CHAT_STOCKS,
  demoCitation,
  demoFeedItem,
  demoInsights,
  FEED_ATTRIBUTION,
  installFakeApi,
  type Options,
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

const install = (options: Options = {}) => installFakeApi({ stocks: CHAT_STOCKS, ...options });

const event = (over: object) => ({ ...demoInsights().events[0]!, ...over });

const TCS_NEWS = demoInsights({
  events: [
    event({
      event_type: 'credit_rating',
      sentiment: 'positive',
      event_date: '2026-09-25',
      summary: 'TCS rating was reaffirmed.',
      citation: demoCitation({
        label: 'Announcement · Announcement under Regulation 30 (LODR)-Credit Rating · p.1',
      }),
    }),
    event({ event_date: '2026-03-01', sentiment: 'negative', summary: 'An older TCS item.' }),
  ],
});
const RELIANCE_NEWS = demoInsights({
  sentiment: { status: 'insufficient_data', score: null, label: null, events_counted: 0 },
  events: [event({ event_date: '2026-07-01', sentiment: 'neutral', summary: 'Reliance item.' })],
});

const tab = (name: string) => screen.getByRole('tab', { name });

describe('the news page', () => {
  it('is in the menu as News, marked current, with the heading and five tabs', async () => {
    install();
    render(<NewsView />);
    const main = await screen.findByRole('navigation', { name: 'Main' });
    expect(within(main).getByRole('link', { name: 'News' })).toHaveAttribute('href', '/news/');
    expect(within(main).getByRole('link', { name: 'News' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(await screen.findByRole('heading', { level: 1, name: 'News' })).toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'All',
      'Reliance',
      'TCS',
      'HDFC Bank',
      'RBI',
    ]);
    expect(tab('All')).toHaveAttribute('aria-selected', 'true');
  });

  it('merges every source on All, newest first', async () => {
    install({
      insights: { TCS: TCS_NEWS, RELIANCE: RELIANCE_NEWS },
      feed: [demoFeedItem({ title: 'RBI middle item', published_at: '2026-08-10T10:00:00Z' })],
    });
    render(<NewsView />);
    await screen.findByText('TCS rating was reaffirmed.');
    await screen.findByText('RBI middle item');
    const items = within(screen.getByRole('tabpanel')).getAllByRole('listitem');
    expect(items.map((i) => i.querySelector('.news-headline')?.textContent)).toEqual([
      'TCS rating was reaffirmed.',
      'RBI middle item',
      'Reliance item.',
      'An older TCS item.',
    ]);
  });

  it('shows each part of an item: headline link, type, date, sentiment words, long source', async () => {
    install({ insights: { TCS: TCS_NEWS } });
    render(<NewsView />);
    await userEvent.click(await screen.findByRole('tab', { name: 'TCS' }));
    const item = (await screen.findByText('TCS rating was reaffirmed.')).closest('li')!;
    expect(within(item).getByRole('link', { name: 'TCS rating was reaffirmed.' })).toHaveAttribute(
      'rel',
      'noopener noreferrer',
    );
    const meta = item.querySelector('.news-meta');
    expect(meta).toHaveTextContent('Credit rating');
    expect(meta).toHaveTextContent('25 Sep 2026');
    expect(meta).toHaveTextContent('Positive');
    expect(item.querySelector('.news-source')).toHaveTextContent(
      'Announcement · Announcement under Regulation 30 (LODR)-Credit Rating · p.1',
    );
  });

  it('shows an event with an unsafe link as plain text', async () => {
    install({
      insights: {
        TCS: demoInsights({
          events: [
            event({
              summary: 'Odd link',
              citation: { ...demoCitation(), url: 'javascript:alert(1)' },
            }),
          ],
        }),
      },
    });
    render(<NewsView />);
    await userEvent.click(await screen.findByRole('tab', { name: 'TCS' }));
    expect(await screen.findByText('Odd link')).toBeInTheDocument();
    expect(within(screen.getByRole('tabpanel')).queryByRole('link')).toBeNull();
  });

  it('shows RBI items with the attribution, links safe ones and never a sample', async () => {
    install({
      feed: [
        demoFeedItem({ title: 'Real release' }),
        demoFeedItem({ title: 'Odd release', url: 'https://evil.example/x' }),
        demoFeedItem({ title: 'Sample release', is_fixture: true }),
      ],
    });
    render(<NewsView />);
    await userEvent.click(await screen.findByRole('tab', { name: 'RBI' }));
    const panel = screen.getByRole('tabpanel');
    expect(await within(panel).findByRole('link', { name: 'Real release' })).toHaveAttribute(
      'href',
      demoFeedItem().url,
    );
    expect(within(panel).getByText('Odd release')).toBeInTheDocument();
    expect(within(panel).queryByRole('link', { name: 'Odd release' })).toBeNull();
    expect(within(panel).queryByRole('link', { name: 'Sample release' })).toBeNull();
    expect(within(panel).getByText('Sample item (offline fixture)')).toBeInTheDocument();
    expect(within(panel).getByText(FEED_ATTRIBUTION)).toBeInTheDocument();
    expect(within(panel).getAllByText(/12 Sep 2026/).length).toBeGreaterThan(0);
  });

  it('shows one stock on its tab with the sentiment summary, or none without one', async () => {
    install({ insights: { TCS: TCS_NEWS, RELIANCE: RELIANCE_NEWS } });
    render(<NewsView />);
    await userEvent.click(await screen.findByRole('tab', { name: 'TCS' }));
    expect(await screen.findByText('An older TCS item.')).toBeInTheDocument();
    expect(screen.queryByText('Reliance item.')).toBeNull();
    expect(
      screen.getByText('News sentiment: positive (0.42 from 5 events in the last year)'),
    ).toBeInTheDocument();

    await userEvent.click(tab('Reliance'));
    expect(await screen.findByText('Reliance item.')).toBeInTheDocument();
    expect(screen.queryByText(/News sentiment/)).toBeNull();
    await userEvent.click(tab('All'));
    expect(screen.queryByText(/News sentiment/)).toBeNull();
  });

  it('says so when a tab has no news', async () => {
    install();
    render(<NewsView />);
    await userEvent.click(await screen.findByRole('tab', { name: 'TCS' }));
    expect(await screen.findByText('No news for TCS yet.')).toBeInTheDocument();
    await userEvent.click(tab('RBI'));
    expect(await screen.findByText('No RBI press releases yet.')).toBeInTheDocument();
    await userEvent.click(tab('All'));
    expect(await screen.findByText('No news yet.')).toBeInTheDocument();
  });

  it('names a failed source without hiding the others', async () => {
    const api = install({ insights: { RELIANCE: RELIANCE_NEWS }, feed: [demoFeedItem()] });
    api.failWith('GET /api/v1/stocks/TCS/insights', 500);
    render(<NewsView />);
    expect(await screen.findByText('Reliance item.')).toBeInTheDocument();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent("couldn't load");
    expect(alert).toHaveTextContent('TCS');
    expect(screen.getByText(demoFeedItem().title)).toBeInTheDocument();
  });

  it('names the RBI feed when only it fails', async () => {
    const api = install({ insights: { RELIANCE: RELIANCE_NEWS } });
    api.failWith('GET /api/v1/feed', 500);
    render(<NewsView />);
    expect(await screen.findByText('Reliance item.')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent('RBI');
  });

  it('moves between the tabs with the arrow keys, Home and End', async () => {
    install();
    render(<NewsView />);
    (await screen.findByRole('tab', { name: 'All' })).focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(tab('Reliance')).toHaveAttribute('aria-selected', 'true');
    expect(tab('Reliance')).toHaveFocus();
    await userEvent.keyboard('{End}');
    expect(tab('RBI')).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{ArrowRight}');
    expect(tab('All')).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{ArrowLeft}');
    expect(tab('RBI')).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{Home}');
    expect(tab('All')).toHaveAttribute('aria-selected', 'true');
  });

  it('sends a signed-out visitor to the sign-in page', async () => {
    install({ signedIn: false });
    render(<NewsView />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('never claims live data or gives advice', async () => {
    install({ insights: { TCS: TCS_NEWS }, feed: [demoFeedItem()] });
    render(<NewsView />);
    await screen.findByText('TCS rating was reaffirmed.');
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/\b(buy|sell)\b/i);
    expect(text.replace(/not live/gi, '')).not.toMatch(/\blive\b/i);
  });
});
