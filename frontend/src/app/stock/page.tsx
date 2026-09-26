import { Suspense } from 'react';

import { StockView } from '@/components/StockView';

// One static page for every stock, told which by ?symbol= (ADR 006: no dynamic routes).
// useSearchParams needs a Suspense boundary so the page can still be exported as static HTML.
export default function StockPage() {
  return (
    <Suspense>
      <StockView />
    </Suspense>
  );
}
