'use client';

import Link from 'next/link';
import { useCallback, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { Activity, BarChart3, BookOpenText, Database, GitCompareArrows, Landmark, Layers3, Scale } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { TerminalShell } from '@/src/components/TerminalShell';
import { TechnicalsWorkspace } from '@/src/components/TechnicalsWorkspace';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { StatusBadge } from '@/src/components/StatusBadge';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { useApi } from '@/src/hooks/useApi';
import { formatDate, formatDateTime, formatPrice } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { FeatureValue, RelativeStrengthMetric, StrategyRuleMetadata } from '@/src/types/api';

const pct = (value: string | null | undefined) => value == null ? '—' : `${(Number(value) * 100).toFixed(2)}%`;
const metricNames: Record<string, string> = {
  REVENUE_TTM: 'Revenue TTM', REVENUE_YOY: 'Revenue YoY', PAT_TTM: 'PAT TTM', PAT_YOY: 'PAT YoY',
  EPS_TTM: 'EPS TTM', NET_MARGIN_TTM: 'Net margin', DEBT_TO_EQUITY: 'Debt / equity', ROE_TTM: 'ROE', FCF_TTM: 'FCF TTM', PE_TTM: 'PE TTM',
};
const percentMetrics = new Set(['REVENUE_YOY', 'PAT_YOY', 'NET_MARGIN_TTM', 'ROE_TTM']);

function IntelligenceValue({ value, reason }: { value: string | null; reason?: string | null }) {
  return <span title={reason ?? undefined}>{value ?? '—'}</span>;
}

function RelativeGrid({ metrics, benchmark }: { metrics: RelativeStrengthMetric[]; benchmark: string }) {
  return <div className="grid gap-px bg-border/70 sm:grid-cols-3">{metrics.map((item) => <div key={item.code} className="bg-background p-3">
    <div className="flex items-center justify-between gap-2"><p className="text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground">{item.sessions} sessions</p><StatusBadge status={item.status} compact /></div>
    <p className="numeric mt-2 text-lg font-semibold"><IntelligenceValue value={pct(item.value)} reason={item.reason} /></p>
    <p className="mt-1 text-[0.6875rem] text-muted-foreground">Stock {pct(item.security_return)} · {benchmark} {pct(item.benchmark_return)}</p>
    {item.percentile != null && <p className="mt-1 text-[0.6875rem] text-muted-foreground">Eligible-universe percentile {Number(item.percentile).toFixed(1)}</p>}
  </div>)}</div>;
}

function FundamentalsPanel({ symbol, asOf }: { symbol: string; asOf: string }) {
  const fundamentals = useApi(useCallback((signal: AbortSignal) => api.fundamentals(symbol, asOf, 'CONSOLIDATED', signal), [symbol, asOf]));
  const metrics = useApi(useCallback((signal: AbortSignal) => api.fundamentalMetrics(symbol, asOf, signal), [symbol, asOf]));
  if (fundamentals.error || metrics.error) return <RequestError message={fundamentals.error ?? metrics.error ?? 'Fundamental request failed'} retry={() => { fundamentals.retry(); metrics.retry(); }} />;
  const byCode = new Map(metrics.data?.metrics.map((item) => [item.code, item]));
  return <div className="space-y-4">
    <Surface>
      <SurfaceHeader eyebrow="Official filings" title="Fundamental intelligence" description="Consolidated is preferred; any standalone fallback is explicit. Missing values are never shown as zero." action={<StatusBadge status={fundamentals.data?.status ?? 'UNAVAILABLE'} />} />
      {metrics.loading ? <div className="p-4"><Skeleton className="h-28 rounded-none" /></div> : <div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-5">{Object.entries(metricNames).map(([code, label]) => { const item = byCode.get(code); const formatted = item?.value == null ? null : percentMetrics.has(code) ? pct(item.value) : Number(item.value).toLocaleString('en-IN', { maximumFractionDigits: 2 }); return <div key={code} className="bg-background p-3"><p className="text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground">{label}</p><p className="numeric mt-2 text-lg font-semibold"><IntelligenceValue value={formatted} reason={item?.reason ?? 'Not available from activated official filings'} /></p><p className="mt-1 text-[0.6875rem] text-muted-foreground">{item?.source_scope ?? 'No activated scope'}</p></div>; })}</div>}
    </Surface>
    <Surface>
      <SurfaceHeader title="Financial history" description="Each row retains reporting scope, cumulative nature, audit state, and point-in-time availability." />
      {fundamentals.loading ? <div className="p-4"><Skeleton className="h-40 rounded-none" /></div> : !fundamentals.data?.filings.length ? <NoResults message="No activated official financial filing is available at this as-of time." /> : <div className="terminal-scrollbar overflow-x-auto"><Table className="min-w-[900px] text-xs"><TableHeader><TableRow><TableHead className="pl-4">Period</TableHead><TableHead>Revenue</TableHead><TableHead>PAT</TableHead><TableHead>EPS</TableHead><TableHead>Scope</TableHead><TableHead>Nature</TableHead><TableHead>Audit</TableHead><TableHead className="pr-4">Available at</TableHead></TableRow></TableHeader><TableBody>{fundamentals.data.filings.map((filing) => { const fact = (code: string) => filing.facts.find((item) => item.normalized_concept === code); return <TableRow key={filing.id}><TableCell className="pl-4">{formatDate(filing.period_end)}</TableCell><TableCell className="numeric">{fact('REVENUE')?.value ?? '—'}</TableCell><TableCell className="numeric">{fact('PROFIT_AFTER_TAX')?.value ?? '—'}</TableCell><TableCell className="numeric">{fact('EPS_BASIC')?.value ?? '—'}</TableCell><TableCell>{filing.scope}</TableCell><TableCell>{fact('REVENUE')?.value_nature ?? filing.reporting_frequency}</TableCell><TableCell>{filing.audit_status}</TableCell><TableCell className="pr-4">{formatDateTime(filing.available_at)}</TableCell></TableRow>; })}</TableBody></Table></div>}
    </Surface>
    <Surface>
      <SurfaceHeader title="Filing provenance" description="Auditable source identity and revision state; no local paths or credentials are exposed." />
      {!fundamentals.data?.filings.length ? <NoResults message="Provenance becomes available when official filings are activated." compact /> : <div className="divide-y divide-border/80">{fundamentals.data.filings.map((filing) => <details key={filing.id} className="group px-4 py-3"><summary className="cursor-pointer text-sm font-medium">{formatDate(filing.period_end)} · {filing.scope} · {filing.revision_status}</summary><dl className="mt-3 grid gap-3 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-4"><div><dt>Source filing</dt><dd className="mt-1 text-foreground">{filing.source_filing_id ?? 'Not supplied'}</dd></div><div><dt>Broadcast / availability</dt><dd className="mt-1 text-foreground">{formatDateTime(filing.available_at)}</dd></div><div><dt>Parser</dt><dd className="mt-1 text-foreground">v{filing.parser_version}</dd></div><div><dt>Fingerprint</dt><dd className="mt-1 font-mono text-foreground">{filing.normalized_fingerprint.slice(0, 12)}…</dd></div></dl></details>)}</div>}
    </Surface>
  </div>;
}

function SectorPanel({ symbol, asOf }: { symbol: string; asOf: string }) {
  const classification = useApi(useCallback((signal: AbortSignal) => api.classification(symbol, asOf, signal), [symbol, asOf]));
  const relative = useApi(useCallback((signal: AbortSignal) => api.relativeStrength(symbol, asOf, signal), [symbol, asOf]));
  if (classification.error || relative.error) return <RequestError message={classification.error ?? relative.error ?? 'Classification request failed'} retry={() => { classification.retry(); relative.retry(); }} />;
  const item = classification.data;
  const peerKey = item?.peers.basic_industry?.length ? 'basic_industry' : item?.peers.industry?.length ? 'industry' : 'sector';
  return <div className="space-y-4">
    <Surface><SurfaceHeader eyebrow="Point-in-time" title="Industry classification" description="Current classifications are visible only after their recorded official retrieval availability." action={<StatusBadge status={item?.status ?? 'UNAVAILABLE'} />} /><dl className="grid sm:grid-cols-2 xl:grid-cols-4">{[['Macro sector', item?.macro_economic_sector], ['Sector', item?.sector], ['Industry', item?.industry], ['Basic industry', item?.basic_industry]].map(([label, value]) => <div key={label} className="border-b border-r border-border/70 p-4"><dt className="text-[0.625rem] uppercase tracking-[0.1em] text-muted-foreground">{label}</dt><dd className="mt-2 text-sm font-medium">{value ?? '—'}</dd></div>)}</dl></Surface>
    <Surface><SurfaceHeader title="Stock vs market" description="Close-to-close return difference over common official sessions; this is descriptive, not predictive." /><RelativeGrid metrics={relative.data?.metrics ?? []} benchmark="NIFTY 200" /></Surface>
    <Surface><SurfaceHeader title="Stock vs sector" description={relative.data?.sector_benchmark ? `Explicit benchmark: ${relative.data.sector_benchmark}` : 'No verified official sector-index mapping is activated.'} action={<StatusBadge status={relative.data?.sector_metrics_status ?? 'UNAVAILABLE'} />} />{relative.data?.sector_metrics_status === 'AVAILABLE' ? <RelativeGrid metrics={relative.data.sector_metrics} benchmark={relative.data.sector_benchmark ?? 'Sector'} /> : <NoResults message="Sector relative strength is unavailable until both an exact mapping and official sector-index history are activated." compact />}</Surface>
    <Surface><SurfaceHeader title="Peers" description={`Deterministic symbol ordering using the most specific available group (${peerKey.replace('_', ' ')}).`} />{!item?.peers[peerKey]?.length ? <NoResults message="No point-in-time peers are available from activated classification data." compact /> : <div className="terminal-scrollbar overflow-x-auto"><Table><TableHeader><TableRow><TableHead className="pl-4">Symbol</TableHead><TableHead>Company</TableHead><TableHead className="pr-4">Group</TableHead></TableRow></TableHeader><TableBody>{item.peers[peerKey].map((peer) => <TableRow key={peer.security_id}><TableCell className="pl-4"><Link className="font-semibold text-primary hover:underline" href={`/securities/${peer.symbol}`}>{peer.symbol}</Link></TableCell><TableCell>{peer.company_name}</TableCell><TableCell className="pr-4">{peerKey.replace('_', ' ')}</TableCell></TableRow>)}</TableBody></Table></div>}</Surface>
  </div>;
}

function compare(actual: FeatureValue, rule: StrategyRuleMetadata): boolean | null {
  if (actual == null) return null;
  const expected = rule.default_expected_value;
  if (typeof actual === 'boolean' || typeof expected === 'boolean') return rule.operator === '=' ? actual === expected : null;
  const left = Number(actual), right = Number(expected);
  if (!Number.isFinite(left) || !Number.isFinite(right)) return null;
  return rule.operator === '>' ? left > right : rule.operator === '>=' ? left >= right : rule.operator === '<' ? left < right : rule.operator === '<=' ? left <= right : left === right;
}

function StrategiesPanel({ symbol }: { symbol: string }) {
  const catalog = useApi(useCallback((signal: AbortSignal) => api.strategyCatalog(signal), []));
  const features = useApi(useCallback((signal: AbortSignal) => api.features(symbol, 'adjusted', signal), [symbol]));
  const latest = features.data?.items.at(-1);
  return <Surface><SurfaceHeader eyebrow="Deterministic rules" title="Strategy state" description="Existing strategy definitions evaluated against the latest single-security feature observation. PASS/FAIL is research state, never a buy/sell label." />{catalog.loading || features.loading ? <div className="p-4"><Skeleton className="h-48 rounded-none" /></div> : catalog.error || features.error ? <RequestError message={catalog.error ?? features.error ?? 'Strategy inputs unavailable'} retry={() => { catalog.retry(); features.retry(); }} /> : <div className="grid gap-px bg-border/70 xl:grid-cols-3">{catalog.data?.strategies.map((strategy) => { const outcomes = strategy.rules.map((rule) => ({ rule, result: compare(latest?.values[rule.feature_code] ?? null, rule) })); const unavailable = outcomes.some((entry) => entry.result == null); const passed = !unavailable && outcomes.every((entry) => entry.result); return <article key={strategy.strategy_code} className="bg-background p-4"><div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold">{strategy.strategy_code} v{strategy.strategy_version}</h3><p className="mt-1 text-xs text-muted-foreground">{strategy.display_name}</p></div><StatusBadge status={unavailable ? 'UNAVAILABLE' : passed ? 'SUCCESS' : 'FAILED'} label={unavailable ? 'UNAVAILABLE' : passed ? 'PASS' : 'FAIL'} compact /></div><div className="mt-4 divide-y divide-border/70 border-y border-border/70">{outcomes.map(({ rule, result }) => <div key={rule.feature_code} className="flex items-center justify-between gap-3 py-2 text-xs"><span className="font-mono">{rule.feature_code} {rule.operator} {String(rule.default_expected_value)}</span><span className={result == null ? 'text-muted-foreground' : result ? 'text-success' : 'text-danger'}>{result == null ? 'UNAVAILABLE' : result ? 'PASS' : 'FAIL'}</span></div>)}</div></article>; })}</div>}</Surface>;
}

function ResearchPanel({ symbol }: { symbol: string }) {
  return <Surface><SurfaceHeader title="Continue research" description="Open an existing AlphaDesk workflow. No expensive backtest is launched from this page." /><div className="grid gap-px bg-border/70 sm:grid-cols-3">{[
    ['/backtests', 'Strategy backtesting', 'Test the unchanged strategy definitions historically.'], ['/portfolio', 'Portfolio research', 'Study capital allocation and portfolio constraints.'], ['/research', 'Composition research', `Compose research for ${symbol} in the existing experiment workspace.`],
  ].map(([href, label, detail]) => <Link key={href} href={href} className="bg-background p-4 transition-colors hover:bg-muted/30"><BookOpenText className="size-4 text-primary" /><h3 className="mt-3 text-sm font-semibold">{label}</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p></Link>)}</div></Surface>;
}

export function SecurityResearchWorkspace() {
  const params = useParams<{ symbol: string }>();
  const symbol = decodeURIComponent(params?.symbol ?? '').toUpperCase();
  const [asOf] = useState(() => new Date().toISOString());
  const security = useApi(useCallback((signal: AbortSignal) => api.security(symbol, signal), [symbol]));
  const summary = useApi(useCallback((signal: AbortSignal) => api.researchSummary(symbol, asOf, signal), [symbol, asOf]));
  const regime = useApi(useCallback((signal: AbortSignal) => api.regimeHistory({
    benchmark: 'NIFTY200',
    start_date: new Date(new Date(asOf).getTime() - 550 * 86_400_000).toISOString().slice(0, 10),
    end_date: asOf.slice(0, 10),
    source_mode: 'OFFICIAL',
    as_of: asOf,
  }, signal), [asOf]));
  const identity = security.data;
  const headline = summary.data;
  const rs = useMemo(() => new Map(headline?.market_relative_strength.map((item) => [item.code, item])), [headline]);

  return <TerminalShell title={symbol || 'Security research'} eyebrow="Research">
    <PageHeader eyebrow="Security Detail 2.0" title={identity?.company_name ?? symbol ?? 'Security'} description="Point-in-time official EOD market, fundamental, classification, relative-strength, technical, and strategy research. No live feed or recommendation is implied." meta={<div className="flex items-center gap-2"><StatusBadge status={headline?.data_source === 'OFFICIAL_NSE_PUBLIC' ? 'ACTIVE' : 'UNAVAILABLE'} label={headline?.data_source === 'OFFICIAL_NSE_PUBLIC' ? 'OFFICIAL NSE' : 'DATA UNAVAILABLE'} /><StatusBadge status={identity?.is_active ? 'ACTIVE' : 'INACTIVE'} /></div>} />
    {security.error || summary.error ? <RequestError message={security.error ?? summary.error ?? 'Security summary request failed'} retry={() => { security.retry(); summary.retry(); }} /> : <>
      <Surface className="mb-4"><div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-6">
        <MetricCard label="Symbol" value={security.loading ? <Skeleton className="h-7 w-20 rounded-none" /> : <span className="text-primary">{identity?.symbol ?? symbol}</span>} supporting={`${identity?.exchange ?? 'NSE'} · ${identity?.currency ?? 'INR'}`} icon={Landmark} accent />
        <MetricCard label="Official close" value={summary.loading ? <Skeleton className="h-7 w-24 rounded-none" /> : headline?.latest_close ? <span className="numeric">{formatPrice.format(Number(headline.latest_close))}</span> : '—'} supporting={headline?.latest_market_date ? `EOD • ${formatDate(headline.latest_market_date)}` : 'No point-in-time close'} icon={BarChart3} />
        <MetricCard label="1D return" value={pct(headline?.one_day_return)} supporting="Previous valid security session" icon={Scale} />
        <MetricCard label="Sector" value={<span className="text-base">{headline?.sector ?? '—'}</span>} supporting={headline?.basic_industry ?? 'Official classification unavailable'} icon={Layers3} />
        <MetricCard label="Market regime" value={<span className="text-base">{regime.data?.latest_classification?.classification.replaceAll('_', ' ') ?? '—'}</span>} supporting={regime.data?.latest_regime_start_date ? `Effective ${formatDate(regime.data.latest_regime_start_date)}` : 'Official NIFTY 200 regime unavailable'} icon={Activity} />
        <MetricCard label="As of" value={<span className="text-base">{formatDate(headline?.latest_market_date)}</span>} supporting="Point-in-time knowledge boundary" icon={Database} />
      </div></Surface>

      <Tabs defaultValue="overview" className="gap-4">
        <div className="terminal-scrollbar overflow-x-auto border-b border-border"><TabsList variant="line" className="h-9 min-w-max gap-4 p-0">
          <TabsTrigger value="overview">Overview</TabsTrigger><TabsTrigger value="fundamentals">Fundamentals</TabsTrigger><TabsTrigger value="sector"><GitCompareArrows className="size-3" />Sector &amp; peers</TabsTrigger><TabsTrigger value="technicals"><Activity className="size-3" />Technicals</TabsTrigger><TabsTrigger value="strategies">Strategies</TabsTrigger><TabsTrigger value="research">Research</TabsTrigger>
        </TabsList></div>
        <TabsContent value="overview" className="space-y-4">
          <Surface><SurfaceHeader title="Research snapshot" description="Bounded top-level composition from the research-summary API." /><div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="RS 1M vs NIFTY 200" value={pct(rs.get('RS_1M_21D')?.value)} supporting={rs.get('RS_1M_21D')?.status ?? 'UNAVAILABLE'} icon={GitCompareArrows} />
            <MetricCard label="RS 3M vs NIFTY 200" value={pct(rs.get('RS_3M_63D')?.value)} supporting={rs.get('RS_3M_63D')?.percentile ? `${Number(rs.get('RS_3M_63D')?.percentile).toFixed(1)} percentile` : 'Percentile unavailable'} icon={GitCompareArrows} />
            <MetricCard label="Fundamentals" value={<StatusBadge status={headline?.fundamental_status ?? 'UNAVAILABLE'} />} supporting="Activated official filings only" icon={Database} />
            <MetricCard label="Classification" value={<StatusBadge status={headline?.classification_status ?? 'UNAVAILABLE'} />} supporting={headline?.basic_industry ?? 'No official snapshot'} icon={Layers3} />
          </div>{!!headline?.warnings.length && <div className="border-t border-border/80 px-4 py-3 text-xs text-warning">{headline.warnings.map((warning) => warning.replaceAll('_', ' ')).join(' · ')}</div>}</Surface>
          <StrategiesPanel symbol={symbol} />
        </TabsContent>
        <TabsContent value="fundamentals"><FundamentalsPanel symbol={symbol} asOf={asOf} /></TabsContent>
        <TabsContent value="sector"><SectorPanel symbol={symbol} asOf={asOf} /></TabsContent>
        <TabsContent value="technicals"><TechnicalsWorkspace symbol={symbol} /></TabsContent>
        <TabsContent value="strategies"><StrategiesPanel symbol={symbol} /></TabsContent>
        <TabsContent value="research"><ResearchPanel symbol={symbol} /></TabsContent>
      </Tabs>
    </>}
  </TerminalShell>;
}
