import { cn } from '@/lib/utils';

export type StatusTone = 'healthy' | 'warning' | 'failed' | 'neutral';

export function statusTone(status: string): StatusTone {
  const normalized = status.toUpperCase();
  if (normalized === 'HEALTHY' || normalized === 'ACTIVE' || normalized === 'SUCCESS' || normalized === 'SUCCEEDED') return 'healthy';
  if (normalized === 'FAILED' || normalized === 'CONFLICT' || normalized === 'UNAVAILABLE' || normalized === 'INACTIVE') return 'failed';
  if (normalized === 'WARNING' || normalized === 'PARTIAL' || normalized === 'DEGRADED' || normalized === 'STALE') return 'warning';
  return 'neutral';
}

const toneClasses: Record<StatusTone, string> = {
  healthy: 'border-success/20 bg-success/8 text-success',
  warning: 'border-warning/25 bg-warning/8 text-warning',
  failed: 'border-danger/25 bg-danger/8 text-danger',
  neutral: 'border-border bg-muted/45 text-muted-foreground',
};

export function StatusDot({ status, className }: { status: string; className?: string }) {
  const tone = statusTone(status);
  return <span aria-hidden="true" className={cn('size-2 shrink-0 rounded-full', tone === 'healthy' && 'bg-success shadow-[0_0_0_3px_color-mix(in_oklab,var(--success)_12%,transparent)]', tone === 'warning' && 'bg-warning shadow-[0_0_0_3px_color-mix(in_oklab,var(--warning)_12%,transparent)]', tone === 'failed' && 'bg-danger shadow-[0_0_0_3px_color-mix(in_oklab,var(--danger)_12%,transparent)]', tone === 'neutral' && 'bg-muted-foreground', className)} />;
}

export function StatusBadge({ status, label, compact = false }: { status: string; label?: string; compact?: boolean }) {
  const normalized = status.toUpperCase();
  const tone = statusTone(status);
  return (
    <span className={cn('inline-flex h-6 items-center gap-2 rounded-full border px-2.5 text-[0.6875rem] font-semibold tracking-[0.08em]', toneClasses[tone], compact && 'h-5 gap-1.5 px-2 text-[0.625rem]')}>
      <StatusDot status={status} className="size-1.5 shadow-none" />
      {label ?? normalized}
    </span>
  );
}
