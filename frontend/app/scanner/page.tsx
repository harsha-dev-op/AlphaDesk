import type { Metadata } from 'next';
import { MarketScannerWorkspace } from '@/src/screens/MarketScannerWorkspace';

export const metadata: Metadata = { title: 'Market Scanner' };

export default function Page() {
  return <MarketScannerWorkspace />;
}
