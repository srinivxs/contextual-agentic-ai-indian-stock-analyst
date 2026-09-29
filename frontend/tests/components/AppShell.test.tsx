import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppShell, initials } from '@/components/AppShell';
import { Monogram, monogramLetters, monogramTint } from '@/components/Monogram';
import { findStocks, stockHref } from '@/components/StockJump';

describe('the app shell', () => {
  it('lists the sections and marks the current one', () => {
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}} active="chat">
        <p>content</p>
      </AppShell>,
    );
    const menu = screen.getByRole('navigation', { name: 'Main' });
    const links = Array.from(menu.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).toEqual(['Home', 'Chat', 'Stocks', 'News', 'Documents', 'Match']);
    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current');
    expect(screen.getByText('content')).toBeInTheDocument();
    expect(screen.getByText(/Not investment advice/)).toBeInTheDocument();
  });

  it('shows who is signed in and signs out', async () => {
    const onSignOut = vi.fn();
    render(
      <AppShell email="reader@example.test" onSignOut={onSignOut}>
        <p>content</p>
      </AppShell>,
    );
    expect(screen.getByText('reader@example.test')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Dark mode' })).toBeInTheDocument();
    expect(screen.getByText('RE')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(onSignOut).toHaveBeenCalledOnce();
  });

  it('makes two letters for the avatar', () => {
    expect(initials('info@example.com')).toBe('IN');
    expect(initials('a.b@example.com')).toBe('AB');
    expect(initials('@example.com')).toBe('?');
  });
});

describe('finding a stock', () => {
  it('matches a ticker or any part of the name, every word', () => {
    expect(findStocks('tcs').map((s) => s.symbol)).toEqual(['TCS']);
    expect(findStocks('HDFC').map((s) => s.symbol)).toEqual(['HDFCBANK']);
    expect(findStocks('tata consult').map((s) => s.symbol)).toEqual(['TCS']);
    expect(findStocks('  ')).toEqual([]);
    expect(findStocks('infosys')).toEqual([]);
  });

  it('links to the stock page', () => {
    expect(stockHref('HDFCBANK')).toBe('/stock/?symbol=HDFCBANK');
  });

  it('suggests matching stocks as you type and says when there are none', async () => {
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}}>
        <p>content</p>
      </AppShell>,
    );
    const box = screen.getByRole('searchbox', { name: 'Find a stock' });
    await userEvent.type(box, 'rel');
    expect(screen.getByRole('link', { name: /RELIANCE/ })).toHaveAttribute(
      'href',
      '/stock/?symbol=RELIANCE',
    );
    await userEvent.clear(box);
    await userEvent.type(box, 'infy');
    expect(screen.getByText(/follows RELIANCE, TCS and HDFC Bank only/)).toBeInTheDocument();
  });

  it('opens the first match on Enter and does nothing without one', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}}>
        <p>content</p>
      </AppShell>,
    );
    const box = screen.getByRole('searchbox', { name: 'Find a stock' });
    await userEvent.type(box, 'zzz{Enter}');
    expect(assign).not.toHaveBeenCalled();
    await userEvent.clear(box);
    await userEvent.type(box, 'tcs{Enter}');
    expect(assign).toHaveBeenCalledWith('/stock/?symbol=TCS');
    vi.unstubAllGlobals();
  });

  it('jumps to the box on "/" unless you are typing elsewhere', async () => {
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}}>
        <input aria-label="other" />
      </AppShell>,
    );
    const box = screen.getByRole('searchbox', { name: 'Find a stock' });
    await userEvent.keyboard('/');
    expect(box).toHaveFocus();
    const other = screen.getByRole('textbox', { name: 'other' });
    await userEvent.click(other);
    await userEvent.keyboard('/');
    expect(other).toHaveFocus();
    expect(other).toHaveValue('/');
  });
});

describe('the stock monogram', () => {
  it('shows two letters in a tint that depends only on the symbol', () => {
    expect(monogramLetters('HDFCBANK')).toBe('HD');
    expect(monogramLetters('M&M')).toBe('MM');
    expect(monogramLetters('&')).toBe('?');
    expect(monogramTint('DEMO')).toBe(monogramTint('DEMO'));
    render(<Monogram symbol="DEMO" size="lg" />);
    expect(screen.getByText('DE')).toHaveClass('monogram', 'lg');
  });

  it.each([
    ['TCS', '/logos/TCS.png'],
    ['RELIANCE', '/logos/RELIANCE.png'],
    ['HDFCBANK', '/logos/HDFCBANK.png'],
  ])('shows the owner-supplied logo for %s instead of letters', (symbol, src) => {
    const { container } = render(<Monogram symbol={symbol} size="md" />);
    const mark = container.querySelector('.monogram');
    expect(mark).toHaveClass('monogram', 'md', 'has-logo');
    const logo = mark?.querySelector('img');
    expect(logo).toHaveAttribute('src', src);
    expect(logo).toHaveAttribute('alt', ''); // decorative: the name is always written next to it
    expect(mark).not.toHaveTextContent(/\w/);
  });
});
