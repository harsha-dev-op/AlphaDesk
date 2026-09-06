import type { Metadata } from 'next';
import { SecuritiesWorkspace } from '@/src/screens/SecuritiesWorkspace';

export const metadata: Metadata = { title: 'Securities' };

export default function Page() {
  return <SecuritiesWorkspace />;
}
