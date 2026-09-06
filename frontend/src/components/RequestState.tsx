import { AlertTriangle, Inbox } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';

export function RequestError({ message, retry, compact = false }: { message: string; retry: () => void; compact?: boolean }) {
  return <Empty className={compact ? 'min-h-40 border-0 bg-danger/[0.035]' : 'min-h-64 border border-danger/20 bg-danger/[0.025]'}><EmptyHeader><EmptyMedia variant="icon"><AlertTriangle className="text-danger" /></EmptyMedia><EmptyTitle>Data could not be loaded</EmptyTitle><EmptyDescription>{message}</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" size="sm" onClick={retry}>Try again</Button></EmptyContent></Empty>;
}

export function NoResults({ message, compact = false }: { message: string; compact?: boolean }) {
  return <Empty className={compact ? 'min-h-40' : 'min-h-64'}><EmptyHeader><EmptyMedia variant="icon"><Inbox /></EmptyMedia><EmptyTitle>No records</EmptyTitle><EmptyDescription>{message}</EmptyDescription></EmptyHeader></Empty>;
}
