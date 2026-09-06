'use client';

import { useCallback } from 'react';
import { AlertTriangle, CheckCircle2, Clock3, Database, RefreshCw, Server, ShieldAlert, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { TerminalShell } from '@/src/components/TerminalShell';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { StatusBadge, StatusDot } from '@/src/components/StatusBadge';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { useApi } from '@/src/hooks/useApi';
import { formatDate, formatDateTime, formatInteger } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { QualityState } from '@/src/types/api';

const statePresentation: Record<QualityState, { icon: typeof CheckCircle2; title: string; className: string }> = {
  HEALTHY: { icon: CheckCircle2, title: 'All configured data controls are clear', className: 'border-success/30 bg-success/[0.045] text-success' },
  WARNING: { icon: AlertTriangle, title: 'Data is usable with explicit caveats', className: 'border-warning/30 bg-warning/[0.055] text-warning' },
  FAILED: { icon: XCircle, title: 'Data controls require immediate attention', className: 'border-danger/30 bg-danger/[0.055] text-danger' },
};

export function DataHealthWorkspace() {
  const health = useApi(useCallback((signal: AbortSignal) => api.health(signal), []));
  const quality = useApi(useCallback((signal: AbortSignal) => api.quality(signal), []));
  const error = health.error || quality.error;
  const presentation = quality.data ? statePresentation[quality.data.status] : statePresentation.WARNING;
  const StateIcon = presentation.icon;
  const firstIssue = quality.data?.checks.find((check) => check.status !== 'HEALTHY');

  const refreshAll = () => {
    health.retry();
    quality.retry();
  };

  return <TerminalShell title="Data health" eyebrow="System">
    <PageHeader eyebrow="Operational controls" title="Data health console" description="Service reachability, validation findings, and freshness evidence for the local AlphaDesk dataset." meta={<Button variant="outline" size="sm" onClick={refreshAll} disabled={health.loading || quality.loading}><RefreshCw className={(health.loading || quality.loading) ? 'animate-spin' : ''} />Refresh checks</Button>} />

    {error ? <RequestError message={error} retry={refreshAll} /> : <>
      <section className={`mb-4 grid gap-4 border px-4 py-4 md:grid-cols-[auto_1fr_auto] md:items-center ${presentation.className}`}>
        <span className="grid size-11 shrink-0 place-items-center border border-current/25 bg-background/20"><StateIcon className="size-5" /></span>
        <div className="min-w-0">{quality.loading ? <div className="space-y-2"><Skeleton className="h-5 w-72 rounded-none" /><Skeleton className="h-4 w-full max-w-xl rounded-none" /></div> : <><h2 className="text-sm font-semibold text-foreground">{presentation.title}</h2><p className="mt-1 text-xs leading-5 text-current/85">{firstIssue?.message ?? 'Every configured validation check completed successfully.'}</p></>}</div>
        <div className="md:text-right">{quality.loading ? <Skeleton className="h-6 w-20 rounded-none" /> : <StatusBadge status={quality.data?.status ?? 'unavailable'} />}<p className="mt-1.5 text-[0.625rem] uppercase tracking-[0.1em] text-muted-foreground">Aggregate posture</p></div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[1.45fr_.55fr]">
        <Surface>
          <SurfaceHeader title="Validation register" description="The worst individual control determines the aggregate posture." action={<span className="numeric text-xs text-muted-foreground">{quality.data ? `${quality.data.checks.length} checks` : '—'}</span>} />
          {quality.loading ? <div className="space-y-2 p-4">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-16 w-full rounded-none" />)}</div> : !quality.data?.checks.length ? <NoResults message="No validation checks were returned by the quality service." compact /> : <div className="divide-y divide-border/80">{quality.data.checks.map((check) => <article key={check.name} className="grid gap-3 px-4 py-3 sm:grid-cols-[auto_1fr_auto] sm:items-center"><StatusDot status={check.status} /><div className="min-w-0"><h3 className="text-sm font-medium">{check.name}</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">{check.message}</p></div><div className="flex items-center gap-3 sm:justify-end"><span className="numeric text-xs text-muted-foreground">{formatInteger.format(check.issue_count)} issues</span><StatusBadge status={check.status} compact /></div></article>)}</div>}
        </Surface>

        <div className="space-y-4">
          <Surface>
            <SurfaceHeader title="Service reachability" description="Live probes against the local stack." />
            <div className="divide-y divide-border/80">{[
              { label: 'API service', status: health.data?.api ?? 'unavailable', detail: health.data ? `Version ${health.data.version}` : 'No response', icon: Server },
              { label: 'Database', status: health.data?.database ?? 'unavailable', detail: 'Connectivity probe', icon: Database },
              { label: 'Quality service', status: quality.data?.status ?? 'unavailable', detail: 'Validation aggregate', icon: ShieldAlert },
            ].map((service) => <div key={service.label} className="flex min-h-16 items-center gap-3 px-4 py-3"><service.icon className="size-4 text-muted-foreground" /><div className="min-w-0 flex-1"><p className="text-sm font-medium">{service.label}</p><p className="mt-0.5 text-xs text-muted-foreground">{service.detail}</p></div>{health.loading || quality.loading ? <Skeleton className="h-5 w-16 rounded-none" /> : <StatusBadge status={service.status} compact />}</div>)}</div>
          </Surface>

          <Surface>
            <SurfaceHeader title="Freshness evidence" description="Dates reported by persisted data and ingestion logs." />
            <dl className="divide-y divide-border/80"><div className="px-4 py-3"><dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Clock3 className="size-3.5" />Latest market session</dt><dd className="numeric mt-2 text-base font-semibold">{quality.loading ? <Skeleton className="h-6 w-32 rounded-none" /> : formatDate(quality.data?.latest_data_date)}</dd></div><div className="px-4 py-3"><dt className="text-xs font-medium text-muted-foreground">Last successful ingestion</dt><dd className="mt-2 text-sm font-medium">{quality.loading ? <Skeleton className="h-5 w-44 rounded-none" /> : formatDateTime(quality.data?.last_successful_ingestion)}</dd></div><div className="flex items-start gap-2.5 bg-warning/[0.045] px-4 py-3 text-xs leading-5 text-warning"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" /><span>The local seed uses deterministic demo data. A stale-data warning is expected when its latest session falls outside the configured freshness window.</span></div></dl>
          </Surface>
        </div>
      </div>

      <p className="mt-3 text-right text-[0.6875rem] text-muted-foreground">Checks evaluated {quality.data ? formatDateTime(quality.data.checked_at) : 'when the quality service responds'}</p>
    </>}
  </TerminalShell>;
}
