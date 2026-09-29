import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { PriceChart } from '@/components/PriceChart';
import { ProfitChart } from '@/components/ProfitChart';
import { Sparkline, linePoints } from '@/components/Sparkline';
import type { SeriesPoint } from '@/lib/series';

const citation = {
  source: 'screener' as const,
  label: 'screener.in · profit-loss · Net Profit · Mar 2026',
  url: 'https://www.screener.in/company/DEMOA/consolidated/',
  quote: null,
};
const point = (period: string, value: string): SeriesPoint => ({ period, value, citation });
const POINTS = [point('FY2024', '80'), point('FY2025', '100'), point('FY2026', '125')];

describe('linePoints', () => {
  it('spreads values across the width and puts the biggest at the top', () => {
    const spots = linePoints([0, 50, 100], 100, 40, 0);
    expect(spots).toEqual([
      [0, 40],
      [50, 20],
      [100, 0],
    ]);
  });

  it('draws a flat line in the middle when every value is the same', () => {
    expect(linePoints([7, 7], 10, 10, 0)).toEqual([
      [0, 5],
      [10, 5],
    ]);
  });

  it('gives nothing for fewer than two values', () => {
    expect(linePoints([], 10, 10, 0)).toEqual([]);
    expect(linePoints([3], 10, 10, 0)).toEqual([]);
  });
});

describe('Sparkline', () => {
  it('is decorative and draws one line', () => {
    const { container } = render(<Sparkline values={[1, 2, 3]} tone="rise" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg?.querySelectorAll('polyline')).toHaveLength(1);
    expect(svg?.getAttribute('class')).toContain('rise');
  });

  it('draws no line for a single value', () => {
    const { container } = render(<Sparkline values={[3]} tone="flat" />);
    expect(container.querySelector('polyline')).toBeNull();
  });
});

describe('ProfitChart', () => {
  it('has an accessible name that says what and which years', () => {
    render(<ProfitChart points={POINTS} name="DemoCo Alpha" label="Net profit" />);
    expect(
      screen.getByRole('img', { name: 'Net profit of DemoCo Alpha, ₹ crore, FY2024 to FY2026' }),
    ).toBeInTheDocument();
  });

  it('carries every point in a visually hidden table', () => {
    render(<ProfitChart points={POINTS} name="DemoCo Alpha" label="Net profit" />);
    const table = screen.getByRole('table', { name: /Net profit of DemoCo Alpha/ });
    const rows = within(table).getAllByRole('row');
    expect(rows.map((r) => r.textContent)).toEqual([
      'PeriodNet profit, ₹ crore',
      'FY2024₹80',
      'FY2025₹100',
      'FY2026₹125',
    ]);
    expect(table.closest('.visually-hidden')).not.toBeNull();
  });

  it('shows the callout with the latest year, its figure and the change on the year before', () => {
    render(<ProfitChart points={POINTS} name="DemoCo Alpha" label="Net profit" />);
    const callout = screen.getByTestId('chart-callout');
    expect(callout).toHaveTextContent('FY2026');
    expect(callout).toHaveTextContent('₹125 crore');
    expect(callout).toHaveTextContent('+25.0% on FY2025');
  });

  it('shows a fall in the fall colour class', () => {
    render(
      <ProfitChart
        points={[point('FY2025', '100'), point('FY2026', '90')]}
        name="X"
        label="Net profit"
      />,
    );
    expect(screen.getByText('-10.0% on FY2025')).toHaveClass('fall');
  });

  it('shows only the figure when there is a single year', () => {
    render(<ProfitChart points={[point('FY2026', '90')]} name="X" label="Net profit" />);
    expect(screen.getByTestId('chart-callout')).toHaveTextContent('₹90 crore');
    expect(screen.queryByText(/on FY/)).toBeNull();
  });

  it('says so instead of drawing an empty chart', () => {
    render(<ProfitChart points={[]} name="DemoCo Alpha" label="Net profit" />);
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByText(/No net profit figures stored yet for DemoCo Alpha/)).toBeVisible();
  });
});

describe('PriceChart', () => {
  const history = [
    { date: '2026-09-24', close: '99' },
    { date: '2026-09-25', close: '100' },
    { date: '2026-09-28', close: '101.5' },
  ];

  it('has an accessible name that says what, in rupees, and which dates', () => {
    render(<PriceChart history={history} name="DemoCo Alpha" changePct="1.5" />);
    expect(
      screen.getByRole('img', {
        name: 'Share price of DemoCo Alpha, ₹, end-of-day closes, 24 Sep 2026 to 28 Sep 2026',
      }),
    ).toBeInTheDocument();
  });

  it('shows the latest close and the day change in the callout', () => {
    render(<PriceChart history={history} name="DemoCo Alpha" changePct="-0.6" />);
    const callout = screen.getByTestId('chart-callout');
    expect(callout).toHaveTextContent('28 Sep 2026');
    expect(callout).toHaveTextContent('₹101.50');
    expect(callout).toHaveTextContent('−0.6% on the day');
  });

  it('leaves the change out when the server sent none', () => {
    render(<PriceChart history={history} name="DemoCo Alpha" changePct={null} />);
    expect(screen.getByTestId('chart-callout')).not.toHaveTextContent('on the day');
  });

  it('holds every close in a hidden table', () => {
    render(<PriceChart history={history} name="DemoCo Alpha" changePct="1.5" />);
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(within(table).getByRole('rowheader', { name: '25 Sep 2026' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: '₹100.00' })).toBeInTheDocument();
  });

  it('says so, and draws nothing, when there are no closes', () => {
    render(<PriceChart history={[]} name="DemoCo Alpha" changePct={null} />);
    expect(screen.getByText('No share prices stored yet for DemoCo Alpha.')).toBeInTheDocument();
    expect(screen.queryByRole('img')).toBeNull();
  });
});
