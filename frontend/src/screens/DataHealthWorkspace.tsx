'use client';

import { useCallback } from 'react';
import { AlertTriangle, CalendarDays, CheckCircle2, Clock3, Database, FileText, Layers3, RefreshCw, Server, ShieldAlert, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { TerminalShell } from '@/src/components/TerminalShell';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { StatusBadge, StatusDot } from '@/src/components/StatusBadge';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { useApi } from '@/src/hooks/useApi';
import { formatDate, formatDateTime, formatInteger } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { DatasetMode, QualityState } from '@/src/types/api';

const statePresentation: Record<QualityState, { icon: typeof CheckCircle2; title: string; className: string }> = {
  HEALTHY: { icon: CheckCircle2, title: 'All configured data controls are clear', className: 'border-success/30 bg-success/[0.045] text-success' },
  WARNING: { icon: AlertTriangle, title: 'Data is usable with explicit caveats', className: 'border-warning/30 bg-warning/[0.055] text-warning' },
  FAILED: { icon: XCircle, title: 'Data controls require immediate attention', className: 'border-danger/30 bg-danger/[0.055] text-danger' },
};

const modeLabels: Record<DatasetMode, { label: string; detail: string }> = {
  DEMO: { label: 'DEMO', detail: 'Only deterministic fictional research data is present.' },
  OFFICIAL_NSE: { label: 'OFFICIAL NSE', detail: 'Official/public NSE artifacts supply the persisted market data.' },
  MIXED: { label: 'MIXED', detail: 'Official/public NSE and deterministic demo records coexist.' },
  UNKNOWN: { label: 'UNKNOWN', detail: 'No recognized data-origin inventory is currently available.' },
};

export function DataHealthWorkspace() {
  const health = useApi(useCallback((signal: AbortSignal) => api.health(signal), []));
  const quality = useApi(useCallback((signal: AbortSignal) => api.quality(signal), []));
  const sources = useApi(useCallback((signal: AbortSignal) => api.dataSources(signal), []));
  const coverage = useApi(useCallback((signal: AbortSignal) => api.dataCoverage(signal), []));
  const loading = health.loading || quality.loading || sources.loading || coverage.loading;
  const error = health.error || quality.error || sources.error || coverage.error;
  const presentation = quality.data ? statePresentation[quality.data.status] : statePresentation.WARNING;
  const StateIcon = presentation.icon;
  const firstIssue = quality.data?.checks.find((check) => check.status !== 'HEALTHY');
  const datasetMode = coverage.data?.mode ?? sources.data?.mode ?? 'UNKNOWN';
  const mode = modeLabels[datasetMode];
  const equityActivation = coverage.data?.activation_datasets.find((item) => item.code === 'EQUITY_EOD');

  const refreshAll = () => {
    health.retry();
    quality.retry();
    sources.retry();
    coverage.retry();
  };

  return <TerminalShell title="Data health" eyebrow="System">
    <PageHeader eyebrow="Operational controls" title="Data health console" description="Reachability, validation, provenance, and official/public EOD coverage for the local AlphaDesk dataset." meta={<Button variant="outline" size="sm" onClick={refreshAll} disabled={loading}><RefreshCw className={loading ? 'animate-spin' : ''} />Refresh checks</Button>} />

    {error ? <RequestError message={error} retry={refreshAll} /> : <>
      <section className={`mb-4 grid gap-4 border px-4 py-4 md:grid-cols-[auto_1fr_auto] md:items-center ${presentation.className}`}>
        <span className="grid size-11 shrink-0 place-items-center border border-current/25 bg-background/20"><StateIcon className="size-5" /></span>
        <div className="min-w-0">{quality.loading ? <div className="space-y-2"><Skeleton className="h-5 w-72 rounded-none" /><Skeleton className="h-4 w-full max-w-xl rounded-none" /></div> : <><h2 className="text-sm font-semibold text-foreground">{presentation.title}</h2><p className="mt-1 text-xs leading-5 text-current/85">{firstIssue?.message ?? 'Every configured validation check completed successfully.'}</p></>}</div>
        <div className="md:text-right">{quality.loading ? <Skeleton className="h-6 w-20 rounded-none" /> : <StatusBadge status={quality.data?.status ?? 'unavailable'} />}<p className="mt-1.5 text-[0.625rem] uppercase tracking-[0.1em] text-muted-foreground">Aggregate posture</p></div>
      </section>

      <Surface className="mb-4">
        <div className="grid divide-y divide-border/75 sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
          <MetricCard label="Dataset mode" value={coverage.loading ? <Skeleton className="h-7 w-28 rounded-none" /> : mode.label} supporting={mode.detail} icon={Layers3} accent />
          <MetricCard label="Official securities" value={coverage.loading ? <Skeleton className="h-7 w-20 rounded-none" /> : formatInteger.format(coverage.data?.official_security_count ?? 0)} supporting={`${formatInteger.format(coverage.data?.demo_security_count ?? 0)} demo records remain`} icon={Database} />
          <MetricCard label="Official price rows" value={coverage.loading ? <Skeleton className="h-7 w-24 rounded-none" /> : formatInteger.format(coverage.data?.official_daily_price_count ?? 0)} supporting={`${formatInteger.format(Number(equityActivation?.metrics.official_sessions ?? 0))} confirmed sessions`} icon={CalendarDays} />
          <MetricCard label="Source artifacts" value={coverage.loading ? <Skeleton className="h-7 w-20 rounded-none" /> : formatInteger.format(coverage.data?.source_artifact_count ?? 0)} supporting={coverage.data?.provider ?? 'No official provider imported'} icon={FileText} />
        </div>
      </Surface>

      <Surface className="mb-4">
        <SurfaceHeader title="Official data activation" description="Readiness is calculated from persisted official rows. Partial never implies complete historical coverage." action={coverage.data && <StatusBadge status={coverage.data.activation_status} />} />
        {coverage.loading ? <div className="grid gap-px bg-border/75 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 7 }, (_, index) => <div key={index} className="bg-background p-4"><Skeleton className="h-20 rounded-none" /></div>)}</div> : <div className="grid gap-px bg-border/75 sm:grid-cols-2 xl:grid-cols-4">{coverage.data?.activation_datasets.map((dataset) => <article key={dataset.code} className="min-w-0 bg-background px-4 py-4"><div className="flex items-start justify-between gap-3"><h3 className="text-sm font-semibold leading-5">{dataset.label}</h3><StatusBadge status={dataset.status} compact /></div><p className="mt-3 numeric text-xl font-semibold">{formatInteger.format(dataset.row_count)}</p><p className="mt-1 text-[0.6875rem] text-muted-foreground">{formatDate(dataset.coverage_start)} — {formatDate(dataset.coverage_end)}</p><p className="mt-3 text-xs leading-5 text-muted-foreground">{dataset.detail}</p>{dataset.warnings[0] && <p className="mt-2 break-words text-[0.6875rem] leading-4 text-warning">{dataset.warnings[0].replaceAll('_', ' ')}</p>}</article>)}</div>}
      </Surface>

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
            <SurfaceHeader title="Coverage window" description="Official EOD dates and latest completed import." />
            <dl className="divide-y divide-border/80">
              <div className="px-4 py-3"><dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Clock3 className="size-3.5" />Latest official price session</dt><dd className="numeric mt-2 text-base font-semibold">{coverage.loading ? <Skeleton className="h-6 w-32 rounded-none" /> : formatDate(coverage.data?.latest_official_price_session)}</dd></div>
              <div className="px-4 py-3"><dt className="text-xs font-medium text-muted-foreground">Earliest official price session</dt><dd className="numeric mt-2 text-sm font-medium">{coverage.loading ? <Skeleton className="h-5 w-32 rounded-none" /> : formatDate(coverage.data?.earliest_official_price_session)}</dd></div>
              <div className="px-4 py-3"><dt className="text-xs font-medium text-muted-foreground">Last successful ingestion</dt><dd className="mt-2 text-sm font-medium">{coverage.loading ? <Skeleton className="h-5 w-44 rounded-none" /> : formatDateTime(coverage.data?.last_successful_ingestion)}</dd></div>
            </dl>
          </Surface>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Surface>
          <SurfaceHeader title="Index membership coverage" description="Public constituent files are labeled as current snapshots, never historical coverage." />
          {coverage.loading ? <div className="space-y-2 p-4"><Skeleton className="h-20 rounded-none" /><Skeleton className="h-20 rounded-none" /></div> : <div className="divide-y divide-border/80">{coverage.data?.index_coverage.map((item) => <article key={item.symbol} className="grid gap-3 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-center"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">{item.label}</h3><StatusBadge status={item.current_snapshot_present ? 'healthy' : 'warning'} label={item.current_snapshot_present ? 'CURRENT SNAPSHOT' : 'NOT IMPORTED'} compact /></div><p className="mt-1 text-xs leading-5 text-muted-foreground">As of {formatDate(item.snapshot_as_of)} · {formatInteger.format(item.member_count)} members · {item.coverage_kind.replaceAll('_', ' ').toLowerCase()}</p></div><span className="text-xs text-warning">{item.warning?.replaceAll('_', ' ') ?? 'No warning'}</span></article>)}</div>}
        </Surface>

        <Surface>
          <SurfaceHeader title="Corporate-action knowledge" description="Only split/bonus rows with trustworthy source publication timestamps are promoted." />
          <div className="grid grid-cols-2 divide-x divide-border/80"><div className="px-4 py-4"><p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Promoted</p><p className="numeric mt-2 text-xl font-semibold">{formatInteger.format(coverage.data?.corporate_actions_promoted ?? 0)}</p></div><div className="px-4 py-4"><p className="text-xs uppercase tracking-[0.08em] text-muted-foreground">Quarantined</p><p className="numeric mt-2 text-xl font-semibold text-warning">{formatInteger.format(coverage.data?.corporate_actions_quarantined ?? 0)}</p></div></div>
          {!!coverage.data?.warnings.length && <div className="border-t border-border/80 bg-warning/[0.035] px-4 py-3"><p className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-warning">Coverage warnings</p><div className="mt-2 flex flex-wrap gap-2">{coverage.data.warnings.map((warning) => <StatusBadge key={warning} status="warning" label={warning.replaceAll('_', ' ')} compact />)}</div></div>}
        </Surface>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.2fr_.8fr]">
        <Surface>
          <SurfaceHeader title="Latest source artifacts" description="Bounded provenance register; raw files remain local and are not downloadable here." />
          {coverage.loading ? <div className="space-y-2 p-4"><Skeleton className="h-16 rounded-none" /><Skeleton className="h-16 rounded-none" /></div> : !coverage.data?.latest_artifacts.length ? <NoResults message="No official/public artifacts have been imported." compact /> : <div className="divide-y divide-border/80">{coverage.data.latest_artifacts.slice(0, 8).map((artifact) => <article key={artifact.id} className="grid gap-2 px-4 py-3 md:grid-cols-[1fr_auto] md:items-center"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">{artifact.artifact_type.replaceAll('_', ' ')}</h3><StatusBadge status={artifact.parse_status} compact /></div><p className="mt-1 break-all font-mono text-[0.6875rem] text-muted-foreground">sha256:{artifact.sha256}</p></div><div className="text-xs text-muted-foreground md:text-right"><p>{formatDate(artifact.source_date)} · {formatInteger.format(artifact.accepted_row_count)} accepted</p><p className="mt-1">{artifact.parser_code} v{artifact.parser_version}</p></div></article>)}</div>}
        </Surface>

        <Surface>
          <SurfaceHeader title="Source contracts" description="Official/public, zero-cost source definitions and parser versions." />
          {sources.loading ? <div className="space-y-2 p-4"><Skeleton className="h-16 rounded-none" /><Skeleton className="h-16 rounded-none" /></div> : <div className="divide-y divide-border/80">{sources.data?.sources.map((source) => <article key={source.artifact_type} className="px-4 py-3"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><h3 className="text-sm font-medium">{source.artifact_kind}</h3><p className="mt-1 text-xs text-muted-foreground">{source.provider} · {source.parser_code} v{source.parser_version}</p></div>{source.latest_artifact_status && <StatusBadge status={source.latest_artifact_status} compact />}</div><p className="mt-2 text-xs leading-5 text-muted-foreground">{source.point_in_time_limit}</p></article>)}</div>}
        </Surface>
      </div>

      {sources.data && <p className="mt-3 text-xs leading-5 text-muted-foreground">{sources.data.redistribution_notice}</p>}
      <p className="mt-3 text-right text-[0.6875rem] text-muted-foreground">Checks evaluated {quality.data ? formatDateTime(quality.data.checked_at) : 'when the quality service responds'}</p>
    </>}
  </TerminalShell>;
}
