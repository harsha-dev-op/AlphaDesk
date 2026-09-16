'use client';

import { useEffect, useMemo, useState } from 'react';
import { Activity, BarChart3, CalendarRange, Database, Fingerprint, Gauge, Layers3, Play, ShieldCheck, TrendingUp } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { StatusBadge } from '@/src/components/StatusBadge';
import { TerminalShell } from '@/src/components/TerminalShell';
import { formatDate, formatInteger, formatPrice } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { RegimeAttributionMetrics, RegimeClassification, RegimeHistoryResponse, RegimeMetadataResponse, RegimeResearchAttributionResponse, RegimeSourceMode } from '@/src/types/api';

const regimeColor: Record<RegimeClassification, string> = {
  TRENDING_BULL: 'var(--success)',
  TRENDING_BEAR: 'var(--danger)',
  SIDEWAYS: 'var(--primary)',
  HIGH_VOLATILITY: 'var(--warning)',
  INSUFFICIENT_HISTORY: 'var(--muted-foreground)',
};

const regimeBg: Record<RegimeClassification, string> = {
  TRENDING_BULL: 'bg-success',
  TRENDING_BEAR: 'bg-danger',
  SIDEWAYS: 'bg-primary',
  HIGH_VOLATILITY: 'bg-warning',
  INSUFFICIENT_HISTORY: 'bg-muted-foreground/45',
};

const pct = (value: string | null) => value === null ? '—' : `${(Number(value) * 100).toFixed(2)}%`;
const money = (value: string | null) => value === null ? '—' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(value));
const decimal = (value: string | null, digits = 4) => value === null ? '—' : Number(value).toFixed(digits);
const label = (value: string) => value.replaceAll('_', ' ');

function Field({ label: fieldLabel, children }: { label: string; children: React.ReactNode }) {
  return <label className="grid min-w-0 gap-1.5 text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted-foreground"><span>{fieldLabel}</span>{children}</label>;
}

function AttributionRow({ item }: { item: RegimeAttributionMetrics }) {
  const classification = item.classification;
  return <TableRow>
    <TableCell className="pl-4 font-semibold"><span className="inline-flex items-center gap-2"><span className={`size-2 ${classification === 'OVERALL' ? 'bg-foreground' : regimeBg[classification]}`} />{label(classification)}</span></TableCell>
    <TableCell className="numeric text-right">{formatInteger.format(item.signal_count)}</TableCell>
    <TableCell className="numeric text-right">{formatInteger.format(item.executed_trade_count)}</TableCell>
    <TableCell className="numeric text-right">{pct(item.win_rate)}</TableCell>
    <TableCell className="numeric text-right">{pct(item.mean_net_return_pct)}</TableCell>
    <TableCell className="numeric pr-4 text-right">{money(item.net_pnl)}</TableCell>
  </TableRow>;
}

export function RegimeWorkspace() {
  const [metadata, setMetadata] = useState<RegimeMetadataResponse | null>(null);
  const [history, setHistory] = useState<RegimeHistoryResponse | null>(null);
  const [attribution, setAttribution] = useState<RegimeResearchAttributionResponse | null>(null);
  const [benchmark, setBenchmark] = useState('');
  const [sourceMode, setSourceMode] = useState<RegimeSourceMode>('DEMO');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [universe, setUniverse] = useState('NIFTYDEMO100');
  const [attributionStart, setAttributionStart] = useState('2025-01-02');
  const [attributionEnd, setAttributionEnd] = useState('2025-03-31');
  const [loadingMetadata, setLoadingMetadata] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingAttribution, setLoadingAttribution] = useState(false);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [attributionError, setAttributionError] = useState<string | null>(null);

  async function loadMetadata() {
    setLoadingMetadata(true);
    setMetadataError(null);
    try {
      const response = await api.regimeMetadata();
      setMetadata(response);
      const preferred = response.benchmarks.find((item) => item.symbol === response.default_benchmark && item.coverage.length)
        ?? response.benchmarks.find((item) => item.coverage.length)
        ?? response.benchmarks[0];
      if (preferred) {
        const coverage = preferred.coverage[0];
        const mode = coverage?.source_mode ?? 'OFFICIAL';
        const first = coverage?.first_available_date ?? '';
        const last = coverage?.last_available_date ?? '';
        setBenchmark(preferred.symbol);
        setSourceMode(mode);
        setStartDate(first);
        setEndDate(last);
        if (first && last) {
          setLoadingHistory(true);
          const timeline = await api.regimeHistory({ benchmark: preferred.symbol, start_date: first, end_date: last, source_mode: mode });
          setHistory(timeline);
        }
      }
    } catch (error) {
      setMetadataError(error instanceof Error ? error.message : 'Market regime metadata could not be loaded.');
    } finally {
      setLoadingMetadata(false);
      setLoadingHistory(false);
    }
  }

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadMetadata(), 0);
    return () => window.clearTimeout(timeout);
  }, []);

  const selectedBenchmark = metadata?.benchmarks.find((item) => item.symbol === benchmark) ?? null;
  const selectedCoverage = selectedBenchmark?.coverage.find((item) => item.source_mode === sourceMode) ?? null;
  const distributionData = useMemo(() => history?.distributions.map((item) => ({ name: label(item.classification), sessions: item.session_count, classification: item.classification })) ?? [], [history]);

  function selectBenchmark(symbol: string) {
    setBenchmark(symbol);
    const item = metadata?.benchmarks.find((candidate) => candidate.symbol === symbol);
    const coverage = item?.coverage[0];
    if (coverage) {
      setSourceMode(coverage.source_mode);
      setStartDate(coverage.first_available_date ?? '');
      setEndDate(coverage.last_available_date ?? '');
    }
  }

  async function runHistory() {
    if (!benchmark || !startDate || !endDate) return;
    setLoadingHistory(true);
    setHistoryError(null);
    try {
      setHistory(await api.regimeHistory({ benchmark, start_date: startDate, end_date: endDate, source_mode: sourceMode }));
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : 'Regime history could not be evaluated.');
    } finally {
      setLoadingHistory(false);
    }
  }

  async function runAttribution() {
    if (!benchmark || !universe || !attributionStart || !attributionEnd) return;
    setLoadingAttribution(true);
    setAttributionError(null);
    try {
      const asOf = `${attributionEnd}T15:30:00+05:30`;
      setAttribution(await api.regimeAttribution({
        benchmark,
        source_mode: sourceMode,
        backtest: {
          source: { inline_composition: {
            policy_code: 'CONSENSUS_N_OF_M', policy_version: '1', universe,
            observation_date: attributionEnd, as_of: asOf, adjustment_policy: 'RAW', required_match_count: 2,
            components: [
              { strategy_code: 'MOMENTUM_TREND', strategy_version: '1', parameter_overrides: {} },
              { strategy_code: 'BREAKOUT_20D', strategy_version: '1', parameter_overrides: {} },
              { strategy_code: 'MEAN_REVERSION_PULLBACK', strategy_version: '1', parameter_overrides: {} },
            ],
          }, experiment_id: null },
          universe, start_date: attributionStart, end_date: attributionEnd, adjustment_policy: 'RAW',
          execution_policy_code: 'COMPOSITION_NEXT_OPEN_FIXED_HOLD', execution_policy_version: '1', holding_sessions: 20,
          slippage_bps: '5', stop_loss_pct: null, profit_target_pct: null, max_exit_delay_sessions: 5,
          cost_model_code: 'INDIA_NSE_CASH_DELIVERY_2026_09', cost_model_version: '1', brokerage_per_order_inr: '0', brokerage_rate: '0', dp_charge_per_scrip_sell_day_inr: '0',
          out_of_sample_start_date: null, diagnostic_detail_limit: 100, trade_notional_inr: '100000', trade_detail_limit: 100,
        },
      }));
    } catch (error) {
      setAttributionError(error instanceof Error ? error.message : 'Regime attribution could not be evaluated.');
    } finally {
      setLoadingAttribution(false);
    }
  }

  const latest = history?.latest_classification ?? null;
  return <TerminalShell title="Market Regime" eyebrow="Point-in-time analytics">
    <PageHeader eyebrow="Phase 11 · explainable research" title="Market Regime" description="Classify benchmark history with deterministic trend, momentum, and relative-volatility rules, then attribute existing composition trades by their signal-date regime. No prediction, ranking, filtering, or position sizing." meta={<div className="flex items-center gap-2"><StatusBadge status="ACTIVE" label="Rule-based" compact />{sourceMode === 'DEMO' && <Badge variant="outline" className="rounded-none border-warning/35 bg-warning/[0.08] text-warning">DEMO DATA</Badge>}</div>} />

    {metadataError && <RequestError message={metadataError} retry={() => void loadMetadata()} />}
    {!metadataError && <div className="grid gap-4">
      <Surface>
        <SurfaceHeader eyebrow="Benchmark and coverage" title="Historical regime timeline" description="Source mode is explicit. OFFICIAL never falls back to DEMO." action={<Button onClick={() => void runHistory()} disabled={loadingHistory || loadingMetadata || !startDate || !endDate} className="rounded-none"><Play />{loadingHistory ? 'Evaluating…' : 'Evaluate history'}</Button>} />
        <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-5">
          <Field label="Benchmark"><select value={benchmark} onChange={(event) => selectBenchmark(event.target.value)} className="h-9 w-full border border-input bg-surface-inset px-2 text-sm outline-none focus:border-primary">{metadata?.benchmarks.map((item) => <option key={item.id} value={item.symbol}>{item.name} · {item.symbol}</option>)}</select></Field>
          <Field label="Source"><select value={sourceMode} onChange={(event) => setSourceMode(event.target.value as RegimeSourceMode)} className="h-9 w-full border border-input bg-surface-inset px-2 text-sm outline-none focus:border-primary">{metadata?.supported_source_modes.map((mode) => <option key={mode} value={mode}>{mode}</option>)}</select></Field>
          <Field label="Start date"><Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></Field>
          <Field label="End date"><Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></Field>
          <div className="border border-border bg-surface-inset/55 px-3 py-2"><p className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Stored coverage</p><p className="numeric mt-1 text-xs font-semibold">{selectedCoverage ? `${formatInteger.format(selectedCoverage.session_count)} sessions` : 'Unavailable'}</p><p className="mt-1 text-[0.625rem] text-muted-foreground">{selectedCoverage ? `${formatDate(selectedCoverage.first_available_date)} — ${formatDate(selectedCoverage.last_available_date)}` : 'No rows for this source'}</p></div>
        </div>
      </Surface>

      {historyError && <RequestError message={historyError} retry={() => void runHistory()} compact />}
      {history?.availability === 'UNAVAILABLE' && <Alert className="rounded-none border-warning/25 bg-warning/[0.04]"><Database /><AlertTitle>Benchmark history unavailable</AlertTitle><AlertDescription>No {sourceMode} index-level history is stored for {selectedBenchmark?.name ?? benchmark}. AlphaDesk did not fabricate or substitute market data.</AlertDescription></Alert>}

      {history?.availability === 'AVAILABLE' && <>
        <Surface>
          <SurfaceHeader eyebrow="Latest available regime" title={`${history.benchmark.name} · ${latest ? label(latest.classification) : 'No classification'}`} description={`${history.coverage.source_mode} source · coverage ${formatDate(history.coverage.first_available_date)} to ${formatDate(history.coverage.last_available_date)}`} action={latest && <span className="inline-flex items-center gap-2 text-xs font-semibold"><span className={`size-2.5 ${regimeBg[latest.classification]}`} />As of {formatDate(latest.observation_date)}</span>} />
          <div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-6">
            <MetricCard label="Regime" value={latest ? <span className="text-base">{label(latest.classification)}</span> : '—'} supporting="Latest stored benchmark session" icon={Activity} accent />
            <MetricCard label="Duration" value={<span className="numeric">{history.latest_regime_duration_sessions}</span>} supporting={`Since ${formatDate(history.latest_regime_start_date)}`} icon={CalendarRange} />
            <MetricCard label="Close" value={<span className="numeric">{latest ? formatPrice.format(Number(latest.close)) : '—'}</span>} supporting="Benchmark closing level" icon={TrendingUp} />
            <MetricCard label="SMA 50 / 200" value={<span className="numeric text-base">{latest ? `${decimal(latest.sma_50, 2)} / ${decimal(latest.sma_200, 2)}` : '—'}</span>} supporting="Strict trend comparisons" icon={Layers3} />
            <MetricCard label="3M momentum" value={latest ? pct(latest.momentum_3m_63d) : '—'} supporting="63 completed sessions" icon={BarChart3} />
            <MetricCard label="20D vol / threshold" value={<span className="numeric text-base">{latest ? `${pct(latest.volatility_20)} / ${pct(latest.historical_volatility_threshold)}` : '—'}</span>} supporting="Prior-only 80th percentile" icon={Gauge} />
          </div>
        </Surface>

        <div className="grid gap-4 xl:grid-cols-[1.4fr_.8fr]">
          <Surface>
            <SurfaceHeader eyebrow="Chronological states" title="Regime timeline" description={`${formatInteger.format(history.classifications.length)} benchmark sessions · hover a band for date and classification`} />
            <div className="terminal-scrollbar overflow-x-auto p-4"><div className="flex h-20 min-w-full" style={{ width: Math.max(history.classifications.length * 3, 720) }}>{history.classifications.map((item) => <span key={item.observation_date} title={`${item.observation_date} · ${label(item.classification)}`} className={`h-full min-w-[3px] border-r border-background/25 ${regimeBg[item.classification]}`} />)}</div></div>
            <div className="flex flex-wrap gap-x-4 gap-y-2 border-t border-border/80 px-4 py-3">{Object.entries(regimeBg).map(([key, color]) => <span key={key} className="inline-flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground"><span className={`size-2 ${color}`} />{label(key)}</span>)}</div>
          </Surface>
          <Surface>
            <SurfaceHeader eyebrow="Historical mix" title="Regime distribution" description="Percentages exclude insufficient-history sessions." />
            <div className="h-72 min-w-0 p-4"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0} initialDimension={{ width: 320, height: 240 }}><BarChart data={distributionData} margin={{ top: 8, right: 8, left: -12, bottom: 54 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="name" angle={-32} textAnchor="end" interval={0} tick={{ fill: 'var(--muted-foreground)', fontSize: 9 }} /><YAxis allowDecimals={false} tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} /><Tooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 0, fontSize: 12 }} /><Bar dataKey="sessions" name="Sessions" fill={regimeColor.SIDEWAYS} /></BarChart></ResponsiveContainer></div>
          </Surface>
        </div>

        <Surface>
          <SurfaceHeader eyebrow="Observed state changes" title="Regime transitions" description="Transitions are descriptive history; no transition probabilities or forecasts are produced." action={<span className="numeric text-xs text-muted-foreground">{history.transitions.length} transitions</span>} />
          {!history.transitions.length ? <NoResults message="No transitions occurred in the requested range." compact /> : <div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[720px] text-xs"><TableHeader><TableRow><TableHead className="pl-4">Date</TableHead><TableHead>Previous</TableHead><TableHead>New</TableHead><TableHead className="text-right">Previous duration</TableHead><TableHead className="pr-4 text-right">Benchmark</TableHead></TableRow></TableHeader><TableBody>{history.transitions.slice(-25).map((item) => <TableRow key={`${item.transition_date}:${item.new_classification}`}><TableCell className="pl-4">{formatDate(item.transition_date)}</TableCell><TableCell>{label(item.previous_classification)}</TableCell><TableCell className="font-semibold">{label(item.new_classification)}</TableCell><TableCell className="numeric text-right">{item.previous_regime_duration_sessions} sessions</TableCell><TableCell className="numeric pr-4 text-right">{item.benchmark_symbol}</TableCell></TableRow>)}</TableBody></Table></div>}
        </Surface>
      </>}

      <Surface>
        <SurfaceHeader eyebrow="Attribution only" title="Composition performance by signal-date regime" description="Runs the existing three-strategy 2-of-3 composition and authoritative Phase 5 backtest unchanged, then groups resulting signals and trades by the benchmark regime on each signal date." action={<Button onClick={() => void runAttribution()} disabled={loadingAttribution || !benchmark} className="rounded-none"><Play />{loadingAttribution ? 'Analyzing…' : 'Run attribution'}</Button>} />
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4"><Field label="Composition universe"><Input value={universe} onChange={(event) => setUniverse(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></Field><Field label="Start date"><Input type="date" value={attributionStart} onChange={(event) => setAttributionStart(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></Field><Field label="End date"><Input type="date" value={attributionEnd} onChange={(event) => setAttributionEnd(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></Field><div className="border border-border bg-surface-inset/55 px-3 py-2"><p className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Execution policy</p><p className="numeric mt-1 text-xs font-semibold">NEXT OPEN · FIXED 20</p><p className="mt-1 text-[0.625rem] text-muted-foreground">No regime filtering or sizing</p></div></div>
        {attributionError && <div className="border-t border-border p-4"><RequestError message={attributionError} retry={() => void runAttribution()} compact /></div>}
      </Surface>

      {attribution && <>
        <Surface>
          <SurfaceHeader eyebrow="Reconciled evidence" title="Overall vs regime attribution" description={`${formatInteger.format(attribution.attributed_trade_count)} trades · ${money(attribution.attributed_net_pnl)} attributed net P&L`} action={<div className="flex gap-2"><StatusBadge status={attribution.trade_count_reconciled ? 'SUCCESS' : 'FAILED'} label="Trades reconcile" compact /><StatusBadge status={attribution.net_pnl_reconciled ? 'SUCCESS' : 'FAILED'} label="P&L reconciles" compact /></div>} />
          <div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[780px] text-xs"><TableHeader><TableRow><TableHead className="pl-4">Regime</TableHead><TableHead className="text-right">Signals</TableHead><TableHead className="text-right">Trades</TableHead><TableHead className="text-right">Win rate</TableHead><TableHead className="text-right">Avg return</TableHead><TableHead className="pr-4 text-right">Net P&amp;L</TableHead></TableRow></TableHeader><TableBody><AttributionRow item={attribution.overall} />{attribution.buckets.map((item) => <AttributionRow key={item.classification} item={item} />)}</TableBody></Table></div>
        </Surface>
        <Surface inset><SurfaceHeader title="Reproducibility and provenance" description={`${attribution.timings.total_service_ms.toFixed(1)} ms total service time`} action={<Fingerprint className="size-4 text-primary" />} /><dl className="grid gap-px bg-border/70 lg:grid-cols-3"><div className="min-w-0 bg-surface-inset p-3"><dt className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Signal fingerprint</dt><dd className="numeric mt-1 break-all text-[0.625rem]">{attribution.backtest.historical_signal_fingerprint}</dd></div><div className="min-w-0 bg-surface-inset p-3"><dt className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Regime timeline</dt><dd className="numeric mt-1 break-all text-[0.625rem]">{attribution.regime_history.regime_timeline_fingerprint}</dd></div><div className="min-w-0 bg-surface-inset p-3"><dt className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Attribution</dt><dd className="numeric mt-1 break-all text-[0.625rem]">{attribution.regime_attribution_fingerprint}</dd></div></dl></Surface>
      </>}

      <Alert className="rounded-none border-primary/20 bg-primary/[0.035]"><ShieldCheck /><AlertTitle>Research classifier, not a market forecast</AlertTitle><AlertDescription>MARKET_REGIME_4_STATE v1 uses only benchmark information available by each session. It never changes AlphaDesk signals, trades, portfolio sizing, or execution.</AlertDescription></Alert>
    </div>}
  </TerminalShell>;
}
