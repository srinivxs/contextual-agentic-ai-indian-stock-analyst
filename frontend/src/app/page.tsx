import { Suspense } from 'react';

import { SignInView } from '@/components/SignInView';

// useSearchParams needs a Suspense boundary so the page can still be exported as static HTML.
export default function Page() {
  return (
    <Suspense>
      <SignInView />
    </Suspense>
  );
}
