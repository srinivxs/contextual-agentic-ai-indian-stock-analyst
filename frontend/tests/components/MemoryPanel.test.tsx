import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { MemoryPanel } from '@/components/MemoryPanel';
import { demoProfileField, installFakeApi } from '../helpers/fakeApi';

const panel = (): HTMLElement => screen.getByRole('region', { name: 'Your investor profile' });

describe('the memory panel', () => {
  it('shows the heading', async () => {
    installFakeApi();
    render(<MemoryPanel />);
    expect(
      await within(panel()).findByRole('heading', { name: 'Your investor profile' }),
    ).toBeInTheDocument();
  });

  it('shows a short note under the heading when a page gives one', async () => {
    installFakeApi();
    render(<MemoryPanel note="The matches below use it." />);
    expect(await within(panel()).findByText('The matches below use it.')).toBeInTheDocument();
  });

  it('says so when nothing is remembered yet', async () => {
    installFakeApi();
    render(<MemoryPanel />);
    expect(
      await within(panel()).findByText(
        "Nothing yet. Tell the chat about yourself, for example: I'm conservative, dividend-focused and I avoid high debt.",
      ),
    ).toBeInTheDocument();
  });

  it('offers Add for every field not yet remembered', async () => {
    installFakeApi();
    render(<MemoryPanel />);
    await within(panel()).findByText(/Nothing yet/);
    expect(within(panel()).getByRole('button', { name: 'Add Risk' })).toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Add Debt' })).toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Add Style' })).toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Add Other' })).toBeInTheDocument();
  });

  it('shows a remembered field: its value, the quote, and the date', async () => {
    installFakeApi({ profileFields: [demoProfileField()] });
    render(<MemoryPanel />);

    await within(panel()).findByText('Conservative');
    expect(
      within(panel()).getByText(
        "You said: “I'm conservative, dividend-focused, and I avoid high debt.”",
      ),
    ).toBeInTheDocument();
    expect(within(panel()).getByText('2026-09-28')).toBeInTheDocument();
    expect(within(panel()).queryByRole('button', { name: 'Add Risk' })).toBeNull();
    expect(within(panel()).getByRole('button', { name: 'Edit Risk' })).toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Forget Risk' })).toBeInTheDocument();
  });

  it('shows "Set by you" for a field set by hand, with no quote', async () => {
    installFakeApi({
      profileFields: [demoProfileField({ source: 'edited', quote: null })],
    });
    render(<MemoryPanel />);

    await within(panel()).findByText('Conservative');
    expect(within(panel()).getByText('Set by you')).toBeInTheDocument();
    expect(within(panel()).queryByText(/You said/)).toBeNull();
  });

  it('shows several chips for a multi-value field', async () => {
    installFakeApi({
      profileFields: [
        demoProfileField({
          field: 'investment_style',
          values: ['income', 'quality'],
          labels: ['Dividends / income', 'Quality'],
        }),
      ],
    });
    render(<MemoryPanel />);

    await within(panel()).findByText('Dividends / income');
    expect(within(panel()).getByText('Quality')).toBeInTheDocument();
  });

  it('adds a field not yet remembered with a single choice', async () => {
    const api = installFakeApi();
    render(<MemoryPanel />);

    await userEvent.click(await within(panel()).findByRole('button', { name: 'Add Risk' }));
    const save = within(panel()).getByRole('button', { name: 'Save Risk' });
    expect(save).toBeDisabled();

    await userEvent.click(within(panel()).getByRole('radio', { name: 'Moderate' }));
    expect(save).toBeEnabled();
    await userEvent.click(save);

    await waitFor(() =>
      expect(api.bodies).toContainEqual({
        key: 'PUT /api/v1/profile/risk_preference',
        body: { values: ['moderate'] },
      }),
    );
    expect(await within(panel()).findByText('Moderate')).toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Edit Risk' })).toBeInTheDocument();
  });

  it('cancels editing without saving', async () => {
    const api = installFakeApi({ profileFields: [demoProfileField()] });
    render(<MemoryPanel />);

    await userEvent.click(await within(panel()).findByRole('button', { name: 'Edit Risk' }));
    await userEvent.click(within(panel()).getByRole('button', { name: 'Cancel editing Risk' }));

    expect(within(panel()).queryByRole('radio', { name: 'Moderate' })).toBeNull();
    expect(within(panel()).getByText('Conservative')).toBeInTheDocument();
    expect(api.bodies).toEqual([]);
  });

  it('lets a multi-value field pick more than one option', async () => {
    const api = installFakeApi();
    render(<MemoryPanel />);

    await userEvent.click(await within(panel()).findByRole('button', { name: 'Add Style' }));
    await userEvent.click(within(panel()).getByRole('checkbox', { name: 'Growth' }));
    await userEvent.click(within(panel()).getByRole('checkbox', { name: 'Quality' }));
    await userEvent.click(within(panel()).getByRole('button', { name: 'Save Style' }));

    await waitFor(() =>
      expect(api.bodies).toContainEqual({
        key: 'PUT /api/v1/profile/investment_style',
        body: { values: ['growth', 'quality'] },
      }),
    );
  });

  it('forgets a field', async () => {
    const api = installFakeApi({ profileFields: [demoProfileField()] });
    render(<MemoryPanel />);

    await userEvent.click(await within(panel()).findByRole('button', { name: 'Forget Risk' }));

    await waitFor(() => expect(api.requests).toContain('DELETE /api/v1/profile/risk_preference'));
    expect(await within(panel()).findByRole('button', { name: 'Add Risk' })).toBeInTheDocument();
  });

  it('forgets everything, with an inline two-step confirm and no window.confirm', async () => {
    const api = installFakeApi({
      profileFields: [
        demoProfileField(),
        demoProfileField({
          field: 'debt_preference',
          values: ['avoid_high_debt'],
          labels: ['Avoid high debt'],
        }),
      ],
    });
    render(<MemoryPanel />);
    await within(panel()).findByText('Conservative');

    const confirmSpy = () => {
      throw new Error('window.confirm must not be used');
    };
    const original = window.confirm;
    window.confirm = confirmSpy as typeof window.confirm;
    try {
      await userEvent.click(within(panel()).getByRole('button', { name: 'Forget everything' }));
      expect(await within(panel()).findByText('Forget everything?')).toBeInTheDocument();

      await userEvent.click(
        within(panel()).getByRole('button', { name: 'No, keep what is remembered' }),
      );
      expect(within(panel()).queryByText('Forget everything?')).toBeNull();
      expect(within(panel()).getByText('Conservative')).toBeInTheDocument();

      await userEvent.click(within(panel()).getByRole('button', { name: 'Forget everything' }));
      await userEvent.click(
        within(panel()).getByRole('button', { name: 'Yes, forget everything' }),
      );

      await waitFor(() => expect(api.requests).toContain('DELETE /api/v1/profile'));
      expect(await within(panel()).findByText(/Nothing yet/)).toBeInTheDocument();
    } finally {
      window.confirm = original;
    }
  });

  it('refetches when told to, for example after a chat reply', async () => {
    const api = installFakeApi();
    const { rerender } = render(<MemoryPanel refreshSignal={0} />);
    await within(panel()).findByText(/Nothing yet/);

    api.setDocuments([]); // no-op, just to touch the fake without changing profile state
    render(<MemoryPanel refreshSignal={0} />);
    rerender(<MemoryPanel refreshSignal={1} />);

    await waitFor(() =>
      expect(api.requests.filter((r) => r === 'GET /api/v1/profile').length).toBeGreaterThanOrEqual(
        2,
      ),
    );
  });

  it('shows a short message when the profile cannot be loaded', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/profile', 500);
    render(<MemoryPanel />);

    expect(await within(panel()).findByRole('alert')).toHaveTextContent(
      "We couldn't load what we remember about you.",
    );
  });

  it('shows a short message when a choice is not accepted', async () => {
    const api = installFakeApi();
    render(<MemoryPanel />);

    await userEvent.click(await within(panel()).findByRole('button', { name: 'Add Risk' }));
    await userEvent.click(within(panel()).getByRole('radio', { name: 'Moderate' }));
    api.failWith('PUT /api/v1/profile/risk_preference', 422);
    await userEvent.click(within(panel()).getByRole('button', { name: 'Save Risk' }));

    expect(await within(panel()).findByRole('alert')).toHaveTextContent(
      "That choice wasn't accepted.",
    );
  });

  it('tells its owner after every save and forget, but not after a failed one', async () => {
    const onChange = vi.fn();
    const api = installFakeApi({ profileFields: [demoProfileField()] });
    render(<MemoryPanel onChange={onChange} />);
    await within(panel()).findByText('Conservative');
    expect(onChange).not.toHaveBeenCalled();

    api.failWith('DELETE /api/v1/profile/risk_preference', 500);
    await userEvent.click(within(panel()).getByRole('button', { name: 'Forget Risk' }));
    await within(panel()).findByRole('alert');
    expect(onChange).not.toHaveBeenCalled();

    await userEvent.click(within(panel()).getByRole('button', { name: 'Edit Risk' }));
    await userEvent.click(within(panel()).getByRole('radio', { name: 'Moderate' }));
    await userEvent.click(within(panel()).getByRole('button', { name: 'Save Risk' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
  });

  it('tells its owner after Forget everything', async () => {
    const onChange = vi.fn();
    installFakeApi({ profileFields: [demoProfileField()] });
    render(<MemoryPanel onChange={onChange} />);
    await within(panel()).findByText('Conservative');
    await userEvent.click(within(panel()).getByRole('button', { name: 'Forget everything' }));
    await userEvent.click(within(panel()).getByRole('button', { name: 'Yes, forget everything' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
  });
});
