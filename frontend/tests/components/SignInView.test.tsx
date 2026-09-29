import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SignInView } from '@/components/SignInView';
import { installFakeApi } from '../helpers/fakeApi';

// The real router object is stable between renders, so the mock's must be too.
const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace }, query: { value: '' } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.query.value),
}));

beforeEach(() => {
  nav.query.value = '';
});

describe('the sign-in page', () => {
  it('offers Sign in with Google as a real link to the backend login route', async () => {
    installFakeApi({ signedIn: false });
    render(<SignInView />);

    const link = await screen.findByRole('link', { name: /sign in with google/i });
    // A plain navigation, not a fetch: the browser must follow the redirects to Google itself.
    expect(link).toHaveAttribute('href', '/api/v1/auth/google/login');
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('does not flash the sign-in link while it is still finding out who you are', () => {
    installFakeApi({ signedIn: false });
    render(<SignInView />);

    expect(screen.queryByRole('link', { name: /sign in with google/i })).not.toBeInTheDocument();
  });

  it('sends a signed-in user straight to the home page', async () => {
    installFakeApi({ signedIn: true });
    render(<SignInView />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/home/'));
  });

  it('shows the brand, one honest line and the not-advice note', async () => {
    installFakeApi({ signedIn: false });
    const { container } = render(<SignInView />);

    expect(
      await screen.findByRole('heading', { name: 'Indian Stock Analyst' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Research on RELIANCE, TCS and HDFC Bank from their official filings'),
    ).toBeInTheDocument();
    expect(screen.getByText(/not investment advice/i)).toBeInTheDocument();
    expect(container.querySelector('.signin-mark svg')).not.toBeNull();
  });

  it('says so, and does not redirect, when the server cannot be reached', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/me', 500);
    render(<SignInView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/something went wrong/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('only ever talks to our own /api/v1', async () => {
    const api = installFakeApi({ signedIn: false });
    render(<SignInView />);
    await screen.findByRole('link', { name: /sign in with google/i });

    expect(api.requests.length).toBeGreaterThan(0);
    for (const request of api.requests) expect(request).toMatch(/^[A-Z]+ \/api\/v1\//);
  });
});

describe('the login_error the backend sends back', () => {
  it('shows a cancelled message', async () => {
    nav.query.value = 'login_error=cancelled';
    installFakeApi({ signedIn: false });
    render(<SignInView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/cancel/i);
    expect(screen.getByRole('link', { name: /sign in with google/i })).toBeInTheDocument();
  });

  it('shows a generic message for a failed login', async () => {
    nav.query.value = 'login_error=login_failed';
    installFakeApi({ signedIn: false });
    render(<SignInView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t sign you in/i);
  });

  it('never renders an unknown value, only the generic message', async () => {
    const hostile = '<img src=x onerror=alert(1)>';
    nav.query.value = `login_error=${encodeURIComponent(hostile)}`;
    installFakeApi({ signedIn: false });
    const { container } = render(<SignInView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t sign you in/i);
    expect(container.innerHTML).not.toContain('onerror');
    expect(container.querySelector('img')).toBeNull();
  });

  it('shows no alert when there was no error', async () => {
    installFakeApi({ signedIn: false });
    render(<SignInView />);

    await screen.findByRole('link', { name: /sign in with google/i });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
