import { Suspense } from 'react';

import { ChatView } from '@/components/ChatView';

// One static page (ADR 006), wrapped in Suspense like the stock page, so the static export keeps
// working if the page ever reads the address (useSearchParams needs the boundary).
export default function ChatPage() {
  return (
    <Suspense>
      <ChatView />
    </Suspense>
  );
}
