'use client';

import { useEffect, useMemo, useState } from 'react';
import { Activity, BarChart3, CalendarRange, Database, Fingerprint, History, IndianRupee, Layers3, Play, ShieldCheck, TrendingDown, TrendingUp, WalletCards } from 'lucide-react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { NoResults } from '@/src/components/RequestState';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { formatDate, formatInteger } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { CompositionBacktestRequest, CompositionBacktestResponse, CompositionEvaluationRequest, CompositionMetadata, CompositionPortfolioRequest, CompositionPortfolioResponse, ExperimentDefinition, ExperimentListItem, HistoricalAnalysisRequest } from '@/src/types/api';

type SourceMode = 'inline' | 'saved';

interface HistoricalResearchPanelProps {
  metadata: CompositionMetadata;
  inlineComposition: CompositionEvaluationRequest | null;
  experiments: ExperimentListItem[];
  initialExperimentId: string | null;
}

const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });
const decimal = (value: string | null | undefined) => value === null || value === undefined ? '—' : Number(value).toFixed(2);
const percentage = (value: string | null | undefined) => value === null || value === undefined ? '—' : `${(Number(value) * 100).toFixed(2)}%`;
const currency = (value: string | null | undefined) => value === null || value === undefined ? '—' : inr.format(Number(value));

function defaultStart(end: string) {
  const value = new Date(`${end}T00:00:00Z`);
  value.setUTCFullYear(value.getUTCFullYear() - 1);
  return value.toISOString().slice(0, 10);
}

function Fingerprints({ signal, config, run }: { signal: string; config: string; run: string }) {
  return <dl className="divide-y divide-border/70 border-t border-border/80">
    {[['Signal', signal], ['Execution config', config], ['Run', run]].map(([label, value]) => <div key={label} className="grid gap-1 px-4 py-2.5 sm:grid-cols-[8rem_1fr]"><dt className="text-[0.625rem] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</dt><dd className="numeric break-all text-[0.625rem]" title={value}>{value}</dd></div>)}
  </dl>;
}

export function HistoricalResearchPanel({ metadata, inlineComposition, experiments, initialExperimentId }: HistoricalResearchPanelProps) {
  const latest = metadata.latest_observation_date ?? new Date().toISOString().slice(0, 10);
  const [sourceMode, setSourceMode] = useState<SourceMode>(initialExperimentId ? 'saved' : 'inline');
  const [experimentId, setExperimentId] = useState(initialExperimentId ?? '');
  const [experiment, setExperiment] = useState<ExperimentDefinition | null>(null);
  const [universe, setUniverse] = useState(metadata.universes[0]?.symbol ?? '');
  const [startDate, setStartDate] = useState(defaultStart(latest));
  const [endDate, setEndDate] = useState(latest);
  const [adjustmentPolicy, setAdjustmentPolicy] = useState<'RAW' | 'ADJUSTED'>('ADJUSTED');
  const [holdingSessions, setHoldingSessions] = useState(20);
  const [slippageBps, setSlippageBps] = useState('5');
  const [stopLoss, setStopLoss] = useState('');
  const [profitTarget, setProfitTarget] = useState('');
  const [tradeNotional, setTradeNotional] = useState('100000');
  const [initialCapital, setInitialCapital] = useState('1000000');
  const [maxPositions, setMaxPositions] = useState(10);
  const [maxWeight, setMaxWeight] = useState('0.10');
  const [grossExposure, setGrossExposure] = useState('1.00');
  const [cashReserve, setCashReserve] = useState('0.00');
  const [backtest, setBacktest] = useState<CompositionBacktestResponse | null>(null);
  const [portfolio, setPortfolio] = useState<CompositionPortfolioResponse | null>(null);
  const [running, setRunning] = useState<'backtest' | 'portfolio' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sourceMode !== 'saved' || !experimentId) return;
    let active = true;
    api.experiment(experimentId).then((value) => {
      if (active) setExperiment(value);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Saved experiment could not be loaded.');
    });
    return () => { active = false; };
  }, [sourceMode, experimentId]);

  const savedExperiment = experiment?.id === experimentId ? experiment : null;
  const sourceSummary = sourceMode === 'saved' ? savedExperiment?.normalized_request : inlineComposition;
  const curve = useMemo(() => portfolio?.daily_equity_curve.map((item) => ({
    date: item.session_date,
    equity: Number(item.portfolio_equity),
    drawdown: Number(item.drawdown_pct ?? 0) * 100,
  })) ?? [], [portfolio]);

  const commonRequest = (): HistoricalAnalysisRequest | null => {
    setError(null);
    if (!universe || !startDate || !endDate || startDate > endDate) {
      setError('Choose a valid universe and historical date range.');
      return null;
    }
    if (holdingSessions < 1 || holdingSessions > 252) {
      setError('Holding period must be between 1 and 252 sessions.');
      return null;
    }
    if (sourceMode === 'inline' && !inlineComposition) {
      setError('Evaluate the current composition once before using it as an inline historical source.');
      return null;
    }
    if (sourceMode === 'saved' && !experimentId) {
      setError('Choose a saved experiment.');
      return null;
    }
    return {
      source: sourceMode === 'inline' ? { inline_composition: inlineComposition } : { experiment_id: experimentId },
      universe,
      start_date: startDate,
      end_date: endDate,
      adjustment_policy: adjustmentPolicy,
      execution_policy_code: 'COMPOSITION_NEXT_OPEN_FIXED_HOLD',
      execution_policy_version: '1',
      holding_sessions: holdingSessions,
      slippage_bps: slippageBps,
      stop_loss_pct: stopLoss || null,
      profit_target_pct: profitTarget || null,
      max_exit_delay_sessions: 5,
      cost_model_code: 'INDIA_NSE_CASH_DELIVERY_2026_09',
      cost_model_version: '1',
      brokerage_per_order_inr: '0',
      brokerage_rate: '0',
      dp_charge_per_scrip_sell_day_inr: '0',
      out_of_sample_start_date: null,
      diagnostic_detail_limit: 250,
    };
  };

  const runBacktest = async () => {
    const common = commonRequest();
    if (!common) return;
    setRunning('backtest');
    try {
      const request: CompositionBacktestRequest = { ...common, trade_notional_inr: tradeNotional, trade_detail_limit: 500 };
      setBacktest(await api.runCompositionBacktest(request));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Historical composition backtest failed.');
    } finally {
      setRunning(null);
    }
  };

  const runPortfolio = async () => {
    const common = commonRequest();
    if (!common) return;
    setRunning('portfolio');
    try {
      const request: CompositionPortfolioRequest = {
        ...common,
        portfolio_policy_code: 'LONG_ONLY_EQUAL_SLOT_PORTFOLIO',
        portfolio_policy_version: '1',
        initial_capital_inr: initialCapital,
        max_concurrent_positions: maxPositions,
        max_position_weight: maxWeight,
        max_gross_exposure: grossExposure,
        minimum_cash_reserve_pct: cashReserve,
        risk_free_rate_annual: '0',
        position_detail_limit: 500,
        ledger_detail_limit: 1000,
      };
      setPortfolio(await api.runCompositionPortfolio(request));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Historical composition portfolio failed.');
    } finally {
      setRunning(null);
    }
  };

  return <div className="grid min-w-0 gap-4 overflow-x-hidden [&>*]:min-w-0 [&_[data-slot=alert-description]]:min-w-0 [&_[data-slot=alert-title]]:min-w-0">
    <Alert className="rounded-none border-warning/25 bg-warning/[0.045] text-warning"><ShieldCheck /><AlertTitle>Research / historical simulation</AlertTitle><AlertDescription>Historical composition outcomes are not predictions or financial advice. Signals execute only through simulated next-session RAW opens; no orders are created.</AlertDescription></Alert>

    <div className="grid min-w-0 gap-4 xl:grid-cols-[1.15fr_.85fr]">
      <Surface>
        <SurfaceHeader eyebrow="Canonical signal source" title="Historical analysis" description="Replay the current normalized composition or an immutable saved experiment across a point-in-time universe." />
        <div className="grid min-w-0 gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3">
          <label htmlFor="historical-source-mode" className="grid gap-1.5 text-xs font-medium">Composition source<Select value={sourceMode} onValueChange={(value) => value && setSourceMode(value as SourceMode)}><SelectTrigger id="historical-source-mode" className="h-9 rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="inline">Current inline composition</SelectItem><SelectItem value="saved">Saved experiment</SelectItem></SelectContent></Select></label>
          {sourceMode === 'saved' ? <label htmlFor="historical-experiment" className="grid gap-1.5 text-xs font-medium sm:col-span-2">Saved experiment<Select value={experimentId} onValueChange={(value) => value && setExperimentId(value)}><SelectTrigger id="historical-experiment" className="h-9 rounded-none bg-surface-inset"><SelectValue placeholder="Choose immutable experiment" /></SelectTrigger><SelectContent>{experiments.map((item) => <SelectItem key={item.id} value={item.id}>{item.name} · {item.required_match_count} of {item.strategy_count}</SelectItem>)}</SelectContent></Select></label> : <div className="border border-border bg-surface-inset/50 p-3 text-xs text-muted-foreground sm:col-span-2">{inlineComposition ? `${inlineComposition.required_match_count} of ${inlineComposition.components.length} · normalized evaluated composition` : 'Evaluate a composition in the Composition tab first.'}</div>}
          <label htmlFor="historical-universe" className="grid gap-1.5 text-xs font-medium">Historical universe<Select value={universe} onValueChange={(value) => value && setUniverse(value)}><SelectTrigger id="historical-universe" className="h-9 rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{metadata.universes.map((item) => <SelectItem key={item.id} value={item.symbol}>{item.name} · {item.symbol}</SelectItem>)}</SelectContent></Select></label>
          <label htmlFor="historical-start" className="grid gap-1.5 text-xs font-medium">Start date<Input id="historical-start" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
          <label htmlFor="historical-end" className="grid gap-1.5 text-xs font-medium">End date<Input id="historical-end" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
          <label htmlFor="historical-adjustment" className="grid gap-1.5 text-xs font-medium">Feature price policy<Select value={adjustmentPolicy} onValueChange={(value) => value && setAdjustmentPolicy(value as 'RAW' | 'ADJUSTED')}><SelectTrigger id="historical-adjustment" className="h-9 rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ADJUSTED">Adjusted features</SelectItem><SelectItem value="RAW">Raw features</SelectItem></SelectContent></Select></label>
          <label htmlFor="historical-holding" className="grid gap-1.5 text-xs font-medium">Holding sessions<Input id="historical-holding" type="number" min={1} max={252} value={holdingSessions} onChange={(event) => setHoldingSessions(Number(event.target.value))} className="h-9 rounded-none bg-surface-inset" /></label>
          <label htmlFor="historical-slippage" className="grid gap-1.5 text-xs font-medium">Slippage · bps<Input id="historical-slippage" inputMode="decimal" value={slippageBps} onChange={(event) => setSlippageBps(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
          <label htmlFor="historical-stop" className="grid gap-1.5 text-xs font-medium">Stop loss · fraction<Input id="historical-stop" inputMode="decimal" value={stopLoss} onChange={(event) => setStopLoss(event.target.value)} placeholder="Optional" className="h-9 rounded-none bg-surface-inset" /></label>
          <label htmlFor="historical-target" className="grid gap-1.5 text-xs font-medium">Profit target · fraction<Input id="historical-target" inputMode="decimal" value={profitTarget} onChange={(event) => setProfitTarget(event.target.value)} placeholder="Optional" className="h-9 rounded-none bg-surface-inset" /></label>
        </div>
      </Surface>

      <Surface inset>
        <SurfaceHeader eyebrow="Immutable composition" title={sourceMode === 'saved' ? savedExperiment?.name ?? 'Select an experiment' : 'Current inline definition'} description="Point-evaluation fields remain preserved as source provenance; the range and execution settings stay separate." />
        {sourceSummary ? <>
          <div className="grid grid-cols-2 gap-px border-t border-border bg-border/70 text-xs"><div className="bg-surface p-3"><p className="text-muted-foreground">Consensus</p><p className="numeric mt-1 font-semibold">{sourceSummary.required_match_count} of {sourceSummary.components.length}</p></div><div className="bg-surface p-3"><p className="text-muted-foreground">Definition clock</p><p className="numeric mt-1 font-semibold">{formatDate(sourceSummary.observation_date)}</p></div></div>
          <div className="divide-y divide-border/70 border-t border-border">{sourceSummary.components.map((component) => <div key={`${component.strategy_code}:${component.strategy_version}`} className="flex items-center justify-between gap-3 px-4 py-2.5 text-xs"><span className="numeric font-semibold">{component.strategy_code}</span><span className="numeric text-muted-foreground">v{component.strategy_version}</span></div>)}</div>
          {(savedExperiment?.config_fingerprint || backtest?.source.composition_config_fingerprint || portfolio?.source.composition_config_fingerprint) && <p className="numeric break-all border-t border-border px-4 py-3 text-[0.625rem] text-muted-foreground">{savedExperiment?.config_fingerprint ?? backtest?.source.composition_config_fingerprint ?? portfolio?.source.composition_config_fingerprint}</p>}
        </> : <NoResults message="No normalized composition source is selected." compact />}
      </Surface>
    </div>

    <div className="grid min-w-0 gap-4 xl:grid-cols-2">
      <Surface><SurfaceHeader eyebrow="Phase 5 reuse" title="Independent composition backtest" description="Canonical setups flow through NEXT_OPEN_FIXED_HOLD and the existing India cash-delivery cost model." /><div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-end"><label htmlFor="historical-notional" className="grid flex-1 gap-1.5 text-xs font-medium">Fixed trade notional · INR<Input id="historical-notional" inputMode="decimal" value={tradeNotional} onChange={(event) => setTradeNotional(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><Button onClick={runBacktest} disabled={running !== null} className="rounded-none"><Play className={running === 'backtest' ? 'animate-pulse' : ''} />{running === 'backtest' ? 'Running…' : 'Run backtest'}</Button></div></Surface>
      <Surface><SurfaceHeader eyebrow="Phase 6 reuse" title="Shared-capital portfolio" description="The same signal fingerprint enters the existing equal-slot cash ledger, exposure controls, and risk analytics." /><div className="grid gap-3 p-4 sm:grid-cols-3"><label htmlFor="historical-capital" className="grid gap-1.5 text-xs font-medium">Initial capital<Input id="historical-capital" inputMode="decimal" value={initialCapital} onChange={(event) => setInitialCapital(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="historical-max-positions" className="grid gap-1.5 text-xs font-medium">Max positions<Input id="historical-max-positions" type="number" min={1} max={500} value={maxPositions} onChange={(event) => setMaxPositions(Number(event.target.value))} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="historical-max-weight" className="grid gap-1.5 text-xs font-medium">Max weight<Input id="historical-max-weight" inputMode="decimal" value={maxWeight} onChange={(event) => setMaxWeight(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="historical-gross" className="grid gap-1.5 text-xs font-medium">Gross exposure<Input id="historical-gross" inputMode="decimal" value={grossExposure} onChange={(event) => setGrossExposure(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="historical-reserve" className="grid gap-1.5 text-xs font-medium">Cash reserve<Input id="historical-reserve" inputMode="decimal" value={cashReserve} onChange={(event) => setCashReserve(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><Button onClick={runPortfolio} disabled={running !== null} className="self-end rounded-none"><WalletCards className={running === 'portfolio' ? 'animate-pulse' : ''} />{running === 'portfolio' ? 'Running…' : 'Run portfolio'}</Button></div></Surface>
    </div>

    {error && <Alert variant="destructive" className="rounded-none"><Activity /><AlertTitle>Historical analysis could not complete</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}

    {backtest && <div className="grid min-w-0 gap-4 [&>*]:min-w-0">
      {backtest.warnings.length > 0 && <Alert className="rounded-none"><Database /><AlertTitle>Backtest diagnostics</AlertTitle><AlertDescription>{backtest.warnings.join(' · ')}</AlertDescription></Alert>}
      <Surface><SurfaceHeader eyebrow="Composition trade analytics" title={`${backtest.composition_policy.display_name} · ${formatDate(backtest.normalized_request.start_date)} to ${formatDate(backtest.normalized_request.end_date)}`} description={`${backtest.dataset.trading_session_count} sessions · ${backtest.dataset.security_count} securities · ${backtest.timings.total_service_ms.toFixed(1)} ms`} /><div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-6"><MetricCard label="Matched setups" value={<span className="numeric">{formatInteger.format(backtest.diagnostics.matched_setups)}</span>} supporting={`${formatInteger.format(backtest.diagnostics.eligible_evaluations)} eligible evaluations`} icon={Layers3} accent /><MetricCard label="Executed trades" value={<span className="numeric">{formatInteger.format(backtest.executed_trade_count)}</span>} supporting={`${formatInteger.format(backtest.skipped_setup_count)} skipped / suppressed`} icon={History} /><MetricCard label="Win rate" value={percentage(backtest.analytics.win_rate)} supporting={`${backtest.analytics.winning_trades} winning trades`} icon={TrendingUp} /><MetricCard label="Net P&L" value={currency(backtest.analytics.total_net_pnl)} supporting={`${currency(backtest.analytics.total_transaction_costs)} modeled costs`} icon={IndianRupee} /><MetricCard label="Average trade" value={percentage(backtest.analytics.average_net_return)} supporting="Net return per closed trade" icon={BarChart3} /><MetricCard label="Cost drag" value={percentage(backtest.analytics.cost_drag_pct)} supporting={`${formatInteger.format(backtest.diagnostics.insufficient_history)} insufficient evaluations`} icon={TrendingDown} /></div></Surface>
      <div className="grid gap-4 xl:grid-cols-[1fr_.42fr]"><Surface><SurfaceHeader title="Simulated trades" description={backtest.trades_truncated ? 'Bounded preview; analytics use the complete trade set.' : 'Entry date, exit, result, and execution provenance.'} /><div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[900px] text-xs"><TableHeader><TableRow><TableHead className="pl-4">Symbol</TableHead><TableHead>Signal / entry</TableHead><TableHead>Exit</TableHead><TableHead>Net P&L</TableHead><TableHead>Net return</TableHead><TableHead className="pr-4">Costs</TableHead></TableRow></TableHeader><TableBody>{backtest.trades.map((trade) => <TableRow key={trade.trade_id}><TableCell className="numeric pl-4 font-semibold">{trade.symbol}</TableCell><TableCell className="numeric">{formatDate(trade.signal_date)} → {formatDate(trade.entry_date)}</TableCell><TableCell>{formatDate(trade.exit_date)} · {trade.exit_reason.replaceAll('_', ' ')}</TableCell><TableCell className="numeric">{currency(trade.net_pnl)}</TableCell><TableCell className="numeric">{percentage(trade.net_return_pct)}</TableCell><TableCell className="numeric pr-4">{currency(trade.total_costs)}</TableCell></TableRow>)}</TableBody></Table></div></Surface><Surface inset><SurfaceHeader title="Backtest provenance" action={<Fingerprint className="size-4 text-primary" />} /><Fingerprints signal={backtest.historical_signal_fingerprint} config={backtest.backtest_config_fingerprint} run={backtest.backtest_run_fingerprint} /><div className="border-t border-border px-4 py-3 text-xs text-muted-foreground">Suppression: {Object.entries(backtest.skipped_setup_reasons).map(([reason, count]) => `${reason.replaceAll('_', ' ')} ${count}`).join(' · ') || 'none'}</div></Surface></div>
    </div>}

    {portfolio && <div className="grid min-w-0 gap-4 [&>*]:min-w-0">
      {portfolio.warnings.length > 0 && <Alert className="rounded-none"><Database /><AlertTitle>Portfolio diagnostics</AlertTitle><AlertDescription>{portfolio.warnings.join(' · ')}</AlertDescription></Alert>}
      <Surface><SurfaceHeader eyebrow="Capital-aware composition research" title={`${portfolio.composition_policy.display_name} · ${portfolio.portfolio_policy.display_name}`} description={`${portfolio.dataset.trading_session_count} sessions · same canonical signal source as backtest`} /><div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-6"><MetricCard label="Ending equity" value={currency(portfolio.metrics.ending_equity)} supporting={`${currency(portfolio.metrics.net_portfolio_pnl)} net P&L`} icon={IndianRupee} accent /><MetricCard label="Total return" value={percentage(portfolio.metrics.total_portfolio_return)} supporting={`CAGR ${percentage(portfolio.metrics.cagr)}`} icon={TrendingUp} /><MetricCard label="Volatility" value={percentage(portfolio.metrics.annualized_volatility)} supporting={`Sharpe ${decimal(portfolio.metrics.sharpe_ratio)}`} icon={BarChart3} /><MetricCard label="Sortino" value={decimal(portfolio.metrics.sortino_ratio)} supporting={`Calmar ${decimal(portfolio.metrics.calmar_ratio)}`} icon={Activity} /><MetricCard label="Max drawdown" value={percentage(portfolio.metrics.drawdown.max_drawdown_pct)} supporting={currency(portfolio.metrics.drawdown.max_drawdown_inr)} icon={TrendingDown} /><MetricCard label="Turnover" value={percentage(portfolio.metrics.portfolio_turnover)} supporting={`${portfolio.rejected_candidate_count} capacity / cash rejections`} icon={WalletCards} /></div></Surface>
      <div className="grid gap-4 xl:grid-cols-2"><Surface><SurfaceHeader eyebrow="Equity curve" title="Daily portfolio equity" description="RAW-close mark-to-market after authoritative session event ordering." /><div className="h-72 min-w-0 p-4"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0} initialDimension={{ width: 320, height: 240 }}><LineChart data={curve}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} minTickGap={32} /><YAxis tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} width={72} domain={['auto', 'auto']} /><Tooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 0, fontSize: 12 }} /><Line type="monotone" dataKey="equity" stroke="var(--primary)" dot={false} strokeWidth={2} /></LineChart></ResponsiveContainer></div></Surface><Surface><SurfaceHeader eyebrow="Risk path" title="Daily drawdown" description="Derived from the existing Phase 6 portfolio equity series." /><div className="h-72 min-w-0 p-4"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0} initialDimension={{ width: 320, height: 240 }}><LineChart data={curve}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} minTickGap={32} /><YAxis tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} unit="%" /><Tooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 0, fontSize: 12 }} /><Line type="monotone" dataKey="drawdown" stroke="var(--danger)" dot={false} strokeWidth={2} /></LineChart></ResponsiveContainer></div></Surface></div>
      <div className="grid gap-4 xl:grid-cols-[1fr_.42fr]"><Surface><SurfaceHeader title="Portfolio positions" description={portfolio.positions_truncated ? 'Bounded preview; metrics use the complete portfolio.' : 'Integer-share positions from the shared-capital ledger.'} /><div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[860px] text-xs"><TableHeader><TableRow><TableHead className="pl-4">Symbol</TableHead><TableHead>Entry</TableHead><TableHead>Exit</TableHead><TableHead>Quantity</TableHead><TableHead>Net P&L</TableHead><TableHead className="pr-4">Contribution</TableHead></TableRow></TableHeader><TableBody>{portfolio.positions.map((position) => <TableRow key={position.position_id}><TableCell className="numeric pl-4 font-semibold">{position.symbol}</TableCell><TableCell>{formatDate(position.entry_date)} · {currency(position.entry_slipped_price)}</TableCell><TableCell>{formatDate(position.exit_date)} · {position.exit_reason.replaceAll('_', ' ')}</TableCell><TableCell className="numeric">{position.initial_quantity}</TableCell><TableCell className="numeric">{currency(position.net_pnl)}</TableCell><TableCell className="numeric pr-4">{percentage(position.portfolio_contribution)}</TableCell></TableRow>)}</TableBody></Table></div></Surface><Surface inset><SurfaceHeader title="Portfolio provenance" action={<Fingerprint className="size-4 text-primary" />} /><Fingerprints signal={portfolio.historical_signal_fingerprint} config={portfolio.portfolio_config_fingerprint} run={portfolio.portfolio_run_fingerprint} /><div className="border-t border-border px-4 py-3 text-xs leading-5 text-muted-foreground"><CalendarRange className="mr-2 inline size-3.5" />{portfolio.accepted_entry_count} accepted · {portfolio.rejected_candidate_count} rejected · average exposure {percentage(portfolio.metrics.average_gross_exposure)}</div></Surface></div>
    </div>}

    {!backtest && !portfolio && !error && <Surface className="border-dashed"><div className="grid min-h-40 place-items-center p-6 text-center"><div><span className="mx-auto grid size-10 place-items-center border border-primary/25 bg-primary/[0.06] text-primary"><CalendarRange className="size-4" /></span><h2 className="mt-3 text-sm font-semibold">Replay one composition through history</h2><p className="mt-1.5 max-w-xl text-sm leading-6 text-muted-foreground">Backtest and portfolio runs remain ephemeral. Their execution fingerprints are separate, while identical signal inputs retain one shared signal fingerprint.</p></div></div></Surface>}
  </div>;
}
