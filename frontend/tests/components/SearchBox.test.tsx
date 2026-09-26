import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SearchBox } from '@/components/SearchBox';
import { demoHit, installFakeApi } from '../helpers/fakeApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

const box = () => render(<SearchBox symbol="DEMOA" stockName="DemoCo Alpha Limited" />);

async function ask(question: string): Promise<void> {
  await userEvent.type(screen.getByRole('searchbox', { name: /search/i }), question);
  await userEvent.click(screen.getByRole('button', { name: 'Search' }));
}

describe('searching one stock’s filings', () => {
  it('is labelled with the stock it searches', () => {
    installFakeApi();
    box();
    expect(
      screen.getByRole('searchbox', {
        name: 'Search DemoCo Alpha Limited’s filings',
      }),
    ).toBeInTheDocument();
  });

  it('needs at least three characters before it can search', async () => {
    installFakeApi();
    box();
    const button = screen.getByRole('button', { name: 'Search' });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByRole('searchbox'), 'ab');
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByRole('searchbox'), 'c');
    expect(button).toBeEnabled();
  });

  it('shows each passage with its filing and page, linked to that page of the original', async () => {
    const hit = demoHit({ page: 7 });
    const api = installFakeApi({ searchHits: [hit] });
    box();

    await ask('employee attrition');

    const results = await screen.findByRole('list', { name: 'Search results' });
    const link = within(results).getByRole('link', {
      name: 'Earnings call · Jul 2026 · page 7',
    });
    expect(link).toHaveAttribute('href', `${hit.source_url}#page=7`);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(within(results).getByText(hit.excerpt)).toBeInTheDocument();
    expect(api.requests).toContain('GET /api/v1/search?q=employee%20attrition&symbol=DEMOA');
  });

  it('can be submitted with Enter', async () => {
    const api = installFakeApi({ searchHits: [demoHit()] });
    box();
    await userEvent.type(screen.getByRole('searchbox'), 'revenue{Enter}');
    await screen.findByRole('list', { name: 'Search results' });
    expect(api.requests).toContain('GET /api/v1/search?q=revenue&symbol=DEMOA');
  });

  it('says so when nothing matches', async () => {
    installFakeApi({ searchHits: [] });
    box();
    await ask('dividend');
    expect(await screen.findByText('No matching passages.')).toBeInTheDocument();
  });

  it('says so while it is searching', async () => {
    const api = installFakeApi({ searchHits: [demoHit()] });
    const release = api.hold('GET /api/v1/search?q=revenue&symbol=DEMOA');
    box();

    await ask('revenue');

    expect(await screen.findByRole('status')).toHaveTextContent('Searching…');
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
    release();
    await screen.findByRole('list', { name: 'Search results' });
  });

  it('says so when search is switched off on the server', async () => {
    installFakeApi({ searchOff: true });
    box();
    await ask('revenue');
    expect(await screen.findByRole('alert')).toHaveTextContent(/switched off/i);
  });

  it('says so when search is unavailable, without any technical detail', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/search?q=revenue&symbol=DEMOA', 503);
    box();
    await ask('revenue');
    expect(await screen.findByRole('alert')).toHaveTextContent(/try again/i);
  });

  it('renders passages as text, never as HTML', async () => {
    installFakeApi({
      searchHits: [demoHit({ excerpt: '<img src=x onerror=alert(1)>' })],
    });
    const { container } = box();
    await ask('revenue');
    await screen.findByText('<img src=x onerror=alert(1)>');
    expect(container.querySelector('img')).toBeNull();
  });

  it('shows a passage without a safe address as plain text', async () => {
    installFakeApi({
      searchHits: [demoHit({ source_url: 'javascript:alert(1)' })],
    });
    box();
    await ask('revenue');
    const results = await screen.findByRole('list', { name: 'Search results' });
    expect(within(results).queryByRole('link')).toBeNull();
    expect(within(results).getByText('Earnings call · Jul 2026 · page 7')).toBeInTheDocument();
  });
});
