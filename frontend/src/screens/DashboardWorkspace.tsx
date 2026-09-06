'use client';

import Link from 'next/link';
import { useCallback } from 'react';
import { Activity, ArrowRight, CalendarClock, CheckCircle2, Database, Layers3, Server, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { TerminalShell } from '@/src/components/TerminalShell';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { StatusBadge, StatusDot } from '@/src/components/StatusBadge';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { useApi } from '@/src/hooks/useApi';
import { formatDate, formatDateTime, formatInteger } from '@/src/lib/format';
import { api } from '@/src/services/api';

export function DashboardWorkspace() {
  const health = useApi(useCallback((signal: AbortSignal) => api.health(signal), []));
  const securities = useApi(useCallback((signal: AbortSignal) => api.securities(1, 6, '', signal), []));
  const quality = useApi(useCallback((signal: AbortSignal) => api.quality(signal), []));
  const anyError = health.error || securities.error || quality.error;
  const healthyChecks = quality.data?.checks.filter((check) => check.status === 'HEALTHY').length ?? 0;
  const firstIssue = quality.data?.checks.find((check) => check.status !== 'HEALTHY');

  return <TerminalShell title="Command center" eyebrow="Overview">
    <PageHeader eyebrow="Market data operations" title="Command center" description="System trust, dataset coverage, and reference-data status at a glance." meta={<div className="flex items-center gap-2 text-xs text-muted-foreground"><CalendarClock className="size-3.5" />Checked {quality.data ? formatDateTime(quality.data.checked_at) : 'when data loads'}</div>} />
    {anyError && <div className="mb-4"><RequestError message="One or more AlphaDesk services could not be reached. Values are marked unavailable instead of being treated as zero." retry={() => { health.retry(); securities.retry(); quality.retry(); }} compact /></div>}

    <div className="grid gap-4 xl:grid-cols-[1.55fr_.85fr]">
      <Surface className="min-h-[15.5rem]">
        <SurfaceHeader eyebrow="System posture" title="Research data readiness" description="The lowest-confidence check sets the overall posture." action={quality.loading ? <Skeleton className="h-6 w-20" /> : <StatusBadge status={quality.data?.status ?? 'unavailable'} />} />
        <div className="grid gap-0 sm:grid-cols-[1.15fr_.85fr]">
          <div className="border-b border-border/80 p-5 sm:border-b-0 sm:border-r">
            {quality.loading ? <div className="space-y-5"><div className="flex items-start gap-4"><Skeleton className="size-11 shrink-0 rounded-none" /><div className="w-full space-y-2"><Skeleton className="h-6 w-52 rounded-none" /><Skeleton className="h-4 w-full max-w-md rounded-none" /></div></div><div className="flex gap-2"><Skeleton className="h-7 w-28 rounded-none" /><Skeleton className="h-7 w-36 rounded-none" /><Skeleton className="h-7 w-28 rounded-none" /></div></div> : <><div className="flex items-start gap-4"><span className="grid size-11 shrink-0 place-items-center border border-warning/25 bg-warning/8 text-warning"><ShieldAlert className="size-5" /></span><div><p className="text-lg font-semibold tracking-tight">{quality.data?.status === 'HEALTHY' ? 'Data controls are clear' : quality.data?.status === 'FAILED' ? 'Data controls require action' : 'Data requires review'}</p><p className="mt-1.5 max-w-2xl text-sm leading-6 text-muted-foreground">{firstIssue?.message ?? 'All configured data validation checks are healthy.'}</p></div></div><div className="mt-5 flex flex-wrap gap-2">{quality.data?.checks.map((check) => <span key={check.name} className="inline-flex items-center gap-2 border border-border/80 bg-surface-inset px-2.5 py-1.5 text-xs"><StatusDot status={check.status} />{check.name}</span>)}</div></>}
          </div>
          <div className="divide-y divide-border/80">
            {[{ label: 'API service', status: health.data?.api ?? 'unavailable', loading: health.loading, icon: Server }, { label: 'Database', status: health.data?.database ?? 'unavailable', loading: health.loading, icon: Database }, { label: 'Data quality', status: quality.data?.status ?? 'unavailable', loading: quality.loading, icon: Activity }].map((item) => <div key={item.label} className="flex min-h-[4.9rem] items-center gap-3 px-4"><item.icon className="size-4 text-muted-foreground" /><div className="min-w-0 flex-1"><p className="text-sm font-medium">{item.label}</p><p className="mt-0.5 text-xs text-muted-foreground">Operational check</p></div>{item.loading ? <Skeleton className="h-5 w-16 rounded-none" /> : <StatusBadge status={item.status} compact />}</div>)}
          </div>
        </div>
      </Surface>

      <Surface>
        <SurfaceHeader eyebrow="Dataset" title="Coverage window" description="The latest persisted AlphaDesk records." />
        <dl className="divide-y divide-border/80">
          <div className="px-4 py-4"><dt className="text-xs uppercase tracking-[0.09em] text-muted-foreground">Latest market session</dt><dd className="numeric mt-2 text-xl font-semibold">{quality.loading ? <Skeleton className="h-7 w-32" /> : formatDate(quality.data?.latest_data_date)}</dd></div>
          <div className="px-4 py-4"><dt className="text-xs uppercase tracking-[0.09em] text-muted-foreground">Last successful ingestion</dt><dd className="mt-2 text-sm font-medium">{quality.loading ? <Skeleton className="h-5 w-44" /> : formatDateTime(quality.data?.last_successful_ingestion)}</dd></div>
          <div className="flex items-start gap-2.5 bg-warning/[0.045] px-4 py-3 text-xs leading-5 text-warning"><ShieldAlert className="mt-0.5 size-3.5 shrink-0" />Historical demo coverage may be intentionally stale; it is never presented as live.</div>
        </dl>
      </Surface>
    </div>

    <div className="mt-4 grid border border-border/90 sm:grid-cols-3">
      <MetricCard label="Tracked securities" value={securities.loading ? <Skeleton className="h-7 w-14" /> : securities.data ? <span className="numeric">{formatInteger.format(securities.data.total)}</span> : 'Unavailable'} supporting="Security-master records" icon={Layers3} accent />
      <MetricCard label="Quality checks clear" value={quality.loading ? <Skeleton className="h-7 w-20" /> : quality.data ? <span className="numeric">{healthyChecks} / {quality.data.checks.length}</span> : 'Unavailable'} supporting="Passing configured controls" icon={CheckCircle2} />
      <MetricCard label="API version" value={health.loading ? <Skeleton className="h-7 w-20" /> : health.data ? <span className="numeric">v{health.data.version}</span> : 'Unavailable'} supporting="Backend contract" icon={Server} />
    </div>

    <div className="mt-4 grid gap-4 xl:grid-cols-[1.35fr_.65fr]">
      <Surface>
        <SurfaceHeader title="Security-master snapshot" description="Reference instruments currently available to research workflows." action={<Button render={<Link href="/securities" />} nativeButton={false} variant="ghost" size="sm" className="text-primary">Open explorer <ArrowRight className="size-3.5" /></Button>} />
        {securities.error ? <RequestError message={securities.error} retry={securities.retry} compact /> : securities.loading ? <div className="space-y-2 p-4">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-9 w-full" />)}</div> : !securities.data?.items.length ? <NoResults message="The security master is empty. Seed or ingest reference data to continue." compact /> : <Table className="text-[0.8125rem]"><TableHeader className="sticky top-0 z-10 bg-surface"><TableRow className="hover:bg-transparent"><TableHead className="h-9 pl-4 text-xs">Symbol</TableHead><TableHead className="h-9 text-xs">Company</TableHead><TableHead className="h-9 text-xs">Sector</TableHead><TableHead className="h-9 text-xs">Exchange</TableHead><TableHead className="h-9 pr-4 text-right text-xs">State</TableHead></TableRow></TableHeader><TableBody>{securities.data.items.map((security) => <TableRow key={security.id} className="interactive-row h-11 hover:bg-primary/[0.035]"><TableCell className="py-2 pl-4"><Link className="focus-terminal numeric font-semibold text-primary hover:underline" href={`/securities/${security.symbol}`}>{security.symbol}</Link></TableCell><TableCell className="max-w-[20rem] truncate py-2 font-medium">{security.company_name}</TableCell><TableCell className="py-2 text-muted-foreground">{security.sector ?? 'Unclassified'}</TableCell><TableCell className="numeric py-2">{security.exchange}</TableCell><TableCell className="py-2 pr-4 text-right"><StatusBadge status={security.is_active ? 'ACTIVE' : 'INACTIVE'} compact /></TableCell></TableRow>)}</TableBody></Table>}
      </Surface>
      <Surface>
        <SurfaceHeader title="Quality register" description="Current checks from the backend quality service." />
        <div className="divide-y divide-border/80">{quality.loading ? <div className="space-y-2 p-4">{Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-11 w-full" />)}</div> : quality.data?.checks.map((check) => <div key={check.name} className="flex items-center gap-3 px-4 py-3"><StatusDot status={check.status} /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{check.name}</p><p className="mt-0.5 truncate text-xs text-muted-foreground">{check.message}</p></div><span className="numeric text-xs text-muted-foreground">{check.issue_count}</span></div>)}</div>
      </Surface>
    </div>
  </TerminalShell>;
}
