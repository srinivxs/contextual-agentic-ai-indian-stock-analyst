import type { ReactNode } from 'react';

import { ChatIcon, FileIcon, HomeIcon, LeafIcon, StarIcon, TargetIcon } from '@/components/Icons';
import { StockJump } from '@/components/StockJump';
import { ThemeToggle } from '@/components/ThemeToggle';

export type Section = 'home' | 'chat' | 'stocks' | 'documents' | 'match';

type Props = {
  email: string;
  onSignOut: () => void;
  /** Which menu item is this page: marked as the current one. */
  active?: Section;
  children: ReactNode;
};

const MENU: { section: Section; href: string; label: string; icon: ReactNode }[] = [
  { section: 'home', href: '/home/', label: 'Home', icon: <HomeIcon /> },
  { section: 'chat', href: '/chat/', label: 'Chat', icon: <ChatIcon /> },
  { section: 'stocks', href: '/stocks/', label: 'Stocks', icon: <StarIcon /> },
  { section: 'documents', href: '/documents/', label: 'Documents', icon: <FileIcon /> },
  { section: 'match', href: '/match/', label: 'Match', icon: <TargetIcon /> },
];

/** "info@example.com" -> "IN": two letters for the avatar. */
export function initials(email: string): string {
  const name = (email.split('@')[0] ?? '').replace(/[^a-z0-9]/gi, '');
  return (name.slice(0, 2) || '?').toUpperCase();
}

/** The frame around every signed-in page: the menu, the stock search, who you are. */
export function AppShell({ email, onSignOut, active, children }: Props) {
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/home/">
          <span className="brand-mark">
            <LeafIcon size={22} />
          </span>
          <span className="brand-text">
            <span className="brand-name">Indian Stock Analyst</span>
            <span className="brand-sub">Research from official filings</span>
          </span>
        </a>
        <nav aria-label="Main" className="menu">
          {MENU.map((item) => (
            <a
              key={item.section}
              href={item.href}
              className="menu-item"
              aria-current={item.section === active ? 'page' : undefined}
            >
              {item.icon}
              <span>{item.label}</span>
            </a>
          ))}
        </nav>
        <p className="sidebar-note">
          Not investment advice. A personal research tool built for demonstration.
        </p>
      </aside>
      <div className="main">
        <header className="topbar">
          <StockJump />
          <div className="who">
            <ThemeToggle />
            <span className="avatar" aria-hidden="true">
              {initials(email)}
            </span>
            <span className="who-email">{email}</span>
            <button type="button" className="button ghost" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </header>
        <main className="page">{children}</main>
      </div>
    </div>
  );
}
