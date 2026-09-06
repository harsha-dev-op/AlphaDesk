import { cn } from '@/lib/utils';

export function MetricCard({ label, value, supporting, icon: Icon, accent = false }: { label: string; value: React.ReactNode; supporting: string; icon: React.ComponentType<{ className?: string }>; accent?: boolean }) {
  return <article className={cn('relative min-w-0 border-r border-border/75 bg-surface px-4 py-3.5 last:border-r-0', accent && 'bg-primary/[0.045]')}><div className="flex items-center justify-between gap-3"><p className="truncate text-xs font-medium uppercase tracking-[0.09em] text-muted-foreground">{label}</p><Icon className={cn('size-4 shrink-0 text-muted-foreground/65', accent && 'text-primary')} /></div><div className="mt-2 min-h-7 text-xl font-semibold tracking-[-0.025em] text-foreground">{value}</div><p className="mt-1 truncate text-xs text-muted-foreground">{supporting}</p></article>;
}
