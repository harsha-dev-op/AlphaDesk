import type { Metadata } from 'next';
import { DataHealthWorkspace } from '@/src/screens/DataHealthWorkspace';

export const metadata: Metadata = { title: 'Data health' };

export default function Page() {
  return <DataHealthWorkspace />;
}
