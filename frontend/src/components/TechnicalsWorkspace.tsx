'use client';

import { useCallback, useMemo, useState } from 'react';
import { Activity, CalendarClock, Database, Gauge, Layers3, Waves } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RequestError, NoResults } from '@/src/components/RequestState';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { useApi } from '@/src/hooks/useApi';
import { formatDate, formatDateTime, formatInteger, formatPrice } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { FeatureDefinition, FeatureValue } from '@/src/types/api';

const categories = [
  { code: 'returns', label: 'Returns', description: 'Observation-lag close returns' },
  { code: 'momentum', label: 'Momentum', description: 'Multi-horizon momentum and Wilder RSI' },
  { code: 'trend', label: 'Trend measures', description: 'Moving averages and close distance' },
  { code: 'volatility', label: 'Volatility & range', description: 'True range, ATR, and annualized dispersion' },
  { code: 'liquidity', label: 'Liquidity', description: 'Volume and traded-value context' },
  { code: 'session', label: 'Session anatomy', description: 'Open, close, and range relationships' },
  { code: 'trend_state', label: 'Descriptive trend state', description: 'Boolean comparisons only—not signals' },
];

const historyCodes = ['RET_1D', 'RET_20D', 'SMA_20', 'RSI_14', 'ATR_PCT_14', 'VOLATILITY_20', 'VOLUME_RATIO_20'];

function formatValue(value: FeatureValue | undefined, definition?: FeatureDefinition): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  switch (definition?.unit) {
    case 'DECIMAL_FRACTION': return `${numeric >= 0 ? '+' : ''}${(numeric * 100).toFixed(2)}%`;
    case 'INDEX_0_100': return numeric.toFixed(1);
    case 'RATIO': return `${numeric.toFixed(2)}×`;
    case 'SHARES': return formatInteger.format(numeric);
    case 'CURRENCY': return `₹${formatInteger.format(numeric)}`;
    case 'PRICE': return formatPrice.format(numeric);
    default: return numeric.toFixed(4);
  }
}

export function TechnicalsWorkspace({ symbol }: { symbol: string }) {
  const [policy, setPolicy] = useState<'raw' | 'adjusted'>('adjusted');
  const catalog = useApi(useCallback((signal: AbortSignal) => api.featureCatalog(signal), []));
  const features = useApi(useCallback((signal: AbortSignal) => api.features(symbol, policy, signal), [symbol, policy]));
  const definitions = useMemo(() => new Map(catalog.data?.definitions.map((definition) => [definition.code, definition]) ?? []), [catalog.data]);
  const latest = features.data?.items.at(-1);
  const recent = features.data?.items.slice(-20).reverse() ?? [];

  if (features.error || catalog.error) return <RequestError message={features.error ?? catalog.error ?? 'Unable to load technical features'} retry={() => { features.retry(); catalog.retry(); }} />;
  if (features.loading || catalog.loading) return <div className="space-y-4"><div className="grid border border-border/90 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 4 }, (_, index) => <div key={index} className="bg-surface p-4"><Skeleton className="h-3 w-24 rounded-none" /><Skeleton className="mt-4 h-8 w-28 rounded-none" /><Skeleton className="mt-2 h-3 w-36 rounded-none" /></div>)}</div><Skeleton className="h-[34rem] w-full rounded-none" /></div>;
  if (!features.data || !latest) return <NoResults message="No point-in-time feature observations are available for this security and availability horizon." />;

  const summary = [
    { label: '20-session return', code: 'RET_20D', icon: Activity, supporting: 'Simple observed-session return' },
    { label: 'Wilder RSI 14', code: 'RSI_14', icon: Gauge, supporting: '0–100 oscillator scale' },
    { label: 'ATR percent 14', code: 'ATR_PCT_14', icon: Waves, supporting: 'ATR divided by close' },
    { label: 'Volume ratio 20', code: 'VOLUME_RATIO_20', icon: Layers3, supporting: 'Current / 20-session average' },
  ];

  return <div className="space-y-4">
    <Surface>
      <SurfaceHeader eyebrow="Phase 2 · feature engine" title="Technical feature snapshot" description="Versioned daily features computed on demand from persisted prices. Descriptive research context only; no signal or recommendation is produced." action={<div className="inline-flex border border-border bg-background p-0.5" aria-label="Feature adjustment policy"><Button size="sm" variant={policy === 'adjusted' ? 'default' : 'ghost'} className="rounded-none" onClick={() => setPolicy('adjusted')}>Adjusted</Button><Button size="sm" variant={policy === 'raw' ? 'default' : 'ghost'} className="rounded-none" onClick={() => setPolicy('raw')}>Raw</Button></div>} />
      <div className="grid gap-2 border-b border-border/80 bg-surface-inset/50 px-4 py-2.5 text-[0.6875rem] text-muted-foreground sm:grid-cols-2 xl:grid-cols-4">
        <span className="inline-flex items-center gap-1.5"><CalendarClock className="size-3 text-primary" />Observed <strong className="font-semibold text-foreground">{formatDate(latest.observation_date)}</strong></span>
        <span>Available <strong className="font-semibold text-foreground">{formatDateTime(latest.available_at)}</strong></span>
        <span>Set <strong className="font-semibold text-foreground">{features.data.feature_set.code} v{features.data.feature_set.version}</strong></span>
        <span>Policy <strong className="font-semibold text-foreground">{features.data.adjustment_policy}</strong></span>
      </div>
      <div className="grid sm:grid-cols-2 xl:grid-cols-4">{summary.map((metric, index) => <MetricCard key={metric.code} label={metric.label} value={<span className="numeric">{formatValue(latest.values[metric.code], definitions.get(metric.code))}</span>} supporting={latest.unavailable[metric.code] ?? metric.supporting} icon={metric.icon} accent={index === 0} />)}</div>
    </Surface>

    {features.data.quality_warnings.length > 0 && <div className="border border-warning/25 bg-warning/[0.06] px-4 py-3 text-xs text-warning">{features.data.quality_warnings.join(' · ')}</div>}

    <div className="grid gap-4 xl:grid-cols-2">{categories.map((category) => {
      const group = catalog.data?.definitions.filter((definition) => definition.category === category.code) ?? [];
      return <Surface key={category.code} className="self-start">
        <SurfaceHeader title={category.label} description={category.description} />
        <div>{group.map((definition) => {
          const value = latest.values[definition.code];
          const unavailable = latest.unavailable[definition.code];
          return <div key={definition.code} className="interactive-row grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-border/65 px-4 py-2.5 last:border-b-0 hover:bg-muted/25" title={definition.formula}>
            <div className="min-w-0"><p className="truncate text-xs font-medium text-foreground">{definition.name}</p><p className="numeric mt-0.5 truncate text-[0.625rem] text-muted-foreground">{definition.code} · min {definition.minimum_observations} obs</p></div>
            <div className="text-right"><p className="numeric text-sm font-semibold text-foreground">{formatValue(value, definition)}</p>{unavailable && <p className="mt-0.5 text-[0.625rem] text-warning">{unavailable}</p>}</div>
          </div>;
        })}</div>
      </Surface>;
    })}</div>

    <Surface>
      <SurfaceHeader title="Recent feature history" description="Latest 20 available observations; percent values use decimal fractions in the API and are formatted here for readability." />
      <div className="terminal-scrollbar max-h-[29rem] overflow-auto"><Table className="min-w-[960px] text-[0.8125rem]"><TableHeader className="sticky top-0 z-10 bg-surface"><TableRow className="hover:bg-transparent"><TableHead className="h-9 pl-4 text-xs">Session</TableHead>{historyCodes.map((code) => <TableHead key={code} className="numeric h-9 text-right text-[0.6875rem]">{code}</TableHead>)}</TableRow></TableHeader><TableBody>{recent.map((row) => <TableRow key={row.observation_date} className="interactive-row h-10"><TableCell className="numeric py-1.5 pl-4 text-xs">{row.observation_date}</TableCell>{historyCodes.map((code) => <TableCell key={code} className="numeric py-1.5 text-right">{formatValue(row.values[code], definitions.get(code))}</TableCell>)}</TableRow>)}</TableBody></Table></div>
      <footer className="grid gap-2 border-t border-border/80 bg-surface-inset/55 px-4 py-3 text-[0.6875rem] text-muted-foreground sm:grid-cols-2 xl:grid-cols-4">
        <span className="inline-flex items-center gap-1.5"><Database className="size-3" />{features.data.dataset.dataset_code}</span>
        <span>Dataset <strong className="font-semibold text-foreground">{features.data.dataset.dataset_version}</strong></span>
        <span>Provider <strong className="font-semibold text-foreground">{features.data.dataset.provider}</strong></span>
        <span className="truncate" title={features.data.dataset.fingerprint}>Fingerprint <strong className="numeric font-semibold text-foreground">{features.data.dataset.fingerprint.slice(0, 12)}…</strong></span>
      </footer>
    </Surface>
  </div>;
}
