'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect } from 'react';

import { LeafIcon } from '@/components/Icons';
import { loginErrorMessage } from '@/lib/loginError';
import { useMe } from '@/lib/session';

/**
 * The landing page. It asks the backend who you are: signed in goes on to the home page,
 * signed out sees the sign-in link. The backend also sends people back here after Google, with
 * ?login_error=... when something went wrong.
 */
export function SignInView() {
  const me = useMe();
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    if (me.status === 'signed-in') router.replace('/home/');
  }, [me.status, router]);

  if (me.status !== 'signed-out' && me.status !== 'error') {
    // Still finding out who you are (or about to move on): show nothing that could flash.
    return (
      <main className="page centered">
        <p role="status" className="muted">
          Loading…
        </p>
      </main>
    );
  }

  const problem =
    me.status === 'error' ? null : loginErrorMessage(params?.get('login_error') ?? null);
  return (
    <main className="signin">
      <div className="signin-card">
        <div className="signin-mark">
          <LeafIcon size={26} />
        </div>
        <h1>Indian Stock Analyst</h1>
        <p className="muted">Research on RELIANCE, TCS and HDFC Bank from their official filings</p>
        {me.status === 'error' && (
          <p role="alert" className="alert">
            Something went wrong reaching the server. Reload the page to try again.
          </p>
        )}
        {problem && (
          <p role="alert" className="alert">
            {problem}
          </p>
        )}
        {/* A plain link, not a fetch: the browser itself must follow the redirects to Google and back. */}
        {me.status === 'signed-out' && (
          <a className="button" href="/api/v1/auth/google/login">
            Sign in with Google
          </a>
        )}
        <p className="signin-note">Not investment advice. A personal research tool.</p>
      </div>
    </main>
  );
}
