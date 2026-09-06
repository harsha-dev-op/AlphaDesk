import type { Metadata } from 'next';
import { StrategiesWorkspace } from '@/src/screens/StrategiesWorkspace';

export const metadata: Metadata = { title: 'Strategy Research' };

export default function Page() {
  return <StrategiesWorkspace />;
}
