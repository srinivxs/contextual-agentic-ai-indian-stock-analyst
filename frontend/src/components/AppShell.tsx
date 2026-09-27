import type { ReactNode } from 'react';

type Props = {
  email: string;
  onSignOut: () => void;
  children: ReactNode;
};

/** The frame around every signed-in page: who you are, how to leave, and the standing notice. */
export function AppShell({ email, onSignOut, children }: Props) {
  return (
    <>
      <header className="header">
        <span className="brand">Indian Stock Analyst</span>
        <nav aria-label="Main" className="nav">
          <a href="/stocks/">Stocks</a>
          <a href="/documents/">Documents</a>
          <a href="/chat/">Chat</a>
        </nav>
        <span className="who">
          <span>{email}</span>
          <button type="button" className="button secondary" onClick={onSignOut}>
            Sign out
          </button>
        </span>
      </header>
      <main className="page">{children}</main>
      <footer className="footer">
        Not investment advice. This is a personal research tool built for demonstration.
      </footer>
    </>
  );
}
