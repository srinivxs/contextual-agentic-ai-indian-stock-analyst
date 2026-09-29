import type { Metadata } from 'next';
import { Plus_Jakarta_Sans } from 'next/font/google';
import type { ReactNode } from 'react';

import { THEME_SCRIPT } from '@/lib/theme';

import './globals.css';

// Self-hosted at build time by next/font: no request to Google from the browser.
const sans = Plus_Jakarta_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-sans',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Indian Stock Analyst',
  description: 'A personal equity-research assistant for three Indian stocks.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The head script sets data-theme before the page draws, so the server's HTML differs from
    // the browser's on that one attribute: expected, hence suppressHydrationWarning.
    <html lang="en" className={sans.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
