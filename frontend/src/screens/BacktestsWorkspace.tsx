'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, BarChart3, Calculator, CalendarRange, Database, FlaskConical, IndianRupee, Layers3, Play, ReceiptText, ShieldCheck, Target, TrendingUp } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { TerminalShell } from '@/src/components/TerminalShell';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { useApi } from '@/src/hooks/useApi';
import { formatDate, formatInteger, formatPrice } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { BacktestCostBreakdown, BacktestPeriodBreakdown, BacktestRunRequest, BacktestRunResponse, BacktestTrade, StrategyMetadata, StrategyParameterMetadata, StrategyScalar } from '@/src/types/api';

function yearBefore(value: string): string {
  const parsed = new Date(`${value}T00:00:00Z`);
  parsed.setUTCFullYear(parsed.getUTCFullYear() - 1);
  return parsed.toISOString().slice(0, 10);
}

function percentage(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function currency(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `₹${formatPrice.format(Number(value))}`;
}

function decimal(value: string | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—';
  return Number(value).toFixed(digits);
}

function defaultsFor(strategy?: StrategyMetadata): Record<string, StrategyScalar> {
  return Object.fromEntries(strategy?.parameters.map((parameter) => [parameter.code, parameter.default_value]) ?? []);
}

function costRows(cost: BacktestRunResponse['cost_analytics']) {
  return [
    ['STT', cost.stt],
    ['NSE transaction', cost.exchange_transaction_charge],
    ['SEBI turnover', cost.sebi_charge],
    ['GST', cost.gst],
    ['Stamp duty', cost.stamp_duty],
    ['Brokerage', cost.brokerage],
    ['DP charge', cost.dp_charge],
  ];
}

function costDetailRows(cost: BacktestCostBreakdown) {
  return [
    ['Turnover', cost.turnover], ['Brokerage', cost.brokerage], ['STT', cost.stt],
    ['Exchange', cost.exchange_transaction_charge], ['SEBI', cost.sebi_charge], ['GST', cost.gst],
    ['Stamp', cost.stamp_duty], ['DP', cost.dp_charge], ['Total', cost.total_charges],
  ];
}

function returnDistribution(trades: BacktestTrade[]) {
  const values = trades.map((trade) => Number(trade.net_return_pct) * 100).filter(Number.isFinite);
  if (!values.length) return [];
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  if (minimum === maximum) return [{ bucket: `${minimum.toFixed(1)}%`, trades: values.length }];
  const width = (maximum - minimum) / 8;
  const bins = Array.from({ length: 8 }, (_, index) => ({
    lower: minimum + index * width,
    upper: minimum + (index + 1) * width,
    trades: 0,
  }));
  for (const value of values) bins[Math.min(Math.floor((value - minimum) / width), 7)].trades += 1;
  return bins.map((bin) => ({ bucket: `${bin.lower.toFixed(1)}–${bin.upper.toFixed(1)}%`, trades: bin.trades }));
}

function PeriodTable({ rows }: { rows: BacktestPeriodBreakdown[] }) {
  if (!rows.length) return <NoResults compact message="No completed trade periods are available for this breakdown." />;
  return <Table><TableHeader><TableRow><TableHead>Period</TableHead><TableHead className="text-right">Setups</TableHead><TableHead className="text-right">Trades</TableHead><TableHead className="text-right">Avg net</TableHead><TableHead className="text-right">Median</TableHead><TableHead className="text-right">Win rate</TableHead><TableHead className="text-right">Profit factor</TableHead><TableHead className="text-right">Net P&amp;L</TableHead><TableHead className="text-right">Costs</TableHead></TableRow></TableHeader><TableBody>{rows.map((row) => <TableRow key={row.label}><TableCell className="font-semibold">{row.label.replaceAll('_', ' ')}</TableCell><TableCell className="numeric text-right">{row.setup_count}</TableCell><TableCell className="numeric text-right">{row.trade_count}</TableCell><TableCell className="numeric text-right">{percentage(row.average_net_return)}</TableCell><TableCell className="numeric text-right">{percentage(row.median_net_return)}</TableCell><TableCell className="numeric text-right">{percentage(row.win_rate)}</TableCell><TableCell className="numeric text-right">{decimal(row.profit_factor)}</TableCell><TableCell className="numeric text-right">{currency(row.total_net_pnl)}</TableCell><TableCell className="numeric text-right">{currency(row.total_costs)}</TableCell></TableRow>)}</TableBody></Table>;
}

export function BacktestsWorkspace() {
  const metadata = useApi(useCallback((signal: AbortSignal) => api.backtestMetadata(signal), []));
  const initialized = useRef(false);
  const [strategyCode, setStrategyCode] = useState('');
  const [strategyVersion, setStrategyVersion] = useState('');
  const [parameters, setParameters] = useState<Record<string, StrategyScalar>>({});
  const [universe, setUniverse] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [adjustmentPolicy, setAdjustmentPolicy] = useState<'RAW' | 'ADJUSTED'>('ADJUSTED');
  const [profileCode, setProfileCode] = useState('');
  const [profileVersion, setProfileVersion] = useState('');
  const [holdingSessions, setHoldingSessions] = useState('');
  const [notional, setNotional] = useState('100000');
  const [slippage, setSlippage] = useState('5');
  const [stopLoss, setStopLoss] = useState('');
  const [profitTarget, setProfitTarget] = useState('');
  const [maxExitDelay, setMaxExitDelay] = useState('5');
  const [costModelCode, setCostModelCode] = useState('');
  const [costModelVersion, setCostModelVersion] = useState('');
  const [brokerageFlat, setBrokerageFlat] = useState('0');
  const [brokerageRate, setBrokerageRate] = useState('0');
  const [dpCharge, setDpCharge] = useState('0');
  const [oosStart, setOosStart] = useState('');
  const [result, setResult] = useState<BacktestRunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const strategiesForCode = useMemo(() => metadata.data?.strategies.filter((item) => item.strategy_code === strategyCode) ?? [], [metadata.data, strategyCode]);
  const strategy = useMemo(() => strategiesForCode.find((item) => item.strategy_version === strategyVersion), [strategiesForCode, strategyVersion]);
  const profiles = useMemo(() => metadata.data?.profiles.filter((item) => item.compatible_strategies.includes(strategyCode)) ?? [], [metadata.data, strategyCode]);
  const profile = useMemo(() => profiles.find((item) => item.profile_code === profileCode && item.profile_version === profileVersion), [profiles, profileCode, profileVersion]);
  const costModels = metadata.data?.cost_models ?? [];
  const distribution = useMemo(() => returnDistribution(result?.trades ?? []), [result]);

  useEffect(() => {
    if (!metadata.data || initialized.current) return;
    initialized.current = true;
    const selectedStrategy = metadata.data.strategies[0];
    const selectedProfile = metadata.data.profiles[0];
    const selectedCost = metadata.data.cost_models[0];
    const latest = metadata.data.latest_observation_date ?? new Date().toISOString().slice(0, 10);
    setStrategyCode(selectedStrategy?.strategy_code ?? '');
    setStrategyVersion(selectedStrategy?.strategy_version ?? '');
    setParameters(defaultsFor(selectedStrategy));
    setUniverse(metadata.data.universes[0]?.symbol ?? '');
    setStartDate(yearBefore(latest));
    setEndDate(latest);
    setProfileCode(selectedProfile?.profile_code ?? '');
    setProfileVersion(selectedProfile?.profile_version ?? '');
    setHoldingSessions(String(selectedProfile?.default_holding_sessions[selectedStrategy?.strategy_code ?? ''] ?? ''));
    setNotional(selectedProfile?.default_trade_notional_inr ?? '100000');
    setSlippage(selectedProfile?.default_slippage_bps ?? '5');
    setMaxExitDelay(String(selectedProfile?.default_max_exit_delay_sessions ?? 5));
    setCostModelCode(selectedCost?.code ?? '');
    setCostModelVersion(selectedCost?.version ?? '');
  }, [metadata.data]);

  const selectStrategy = (code: string) => {
    const selected = metadata.data?.strategies.find((item) => item.strategy_code === code);
    const compatibleProfile = metadata.data?.profiles.find((item) => item.compatible_strategies.includes(code));
    setStrategyCode(code);
    setStrategyVersion(selected?.strategy_version ?? '');
    setParameters(defaultsFor(selected));
    if (compatibleProfile) {
      setProfileCode(compatibleProfile.profile_code);
      setProfileVersion(compatibleProfile.profile_version);
      setHoldingSessions(String(compatibleProfile.default_holding_sessions[code]));
    }
    setResult(null);
  };

  const updateParameter = (parameter: StrategyParameterMetadata, value: string) => {
    setParameters((current) => ({ ...current, [parameter.code]: parameter.value_type === 'BOOLEAN' ? value === 'true' : value }));
  };

  const runBacktest = async () => {
    if (!strategy || !profile || !universe || !startDate || !endDate || !costModelCode || !costModelVersion) {
      setRunError('Complete the strategy, universe, dates, profile, and cost model before running the research backtest.');
      return;
    }
    if (startDate > endDate) {
      setRunError('Start date must be on or before end date.');
      return;
    }
    const numericFields = [
      ['Holding sessions', holdingSessions], ['Trade notional', notional], ['Slippage', slippage],
      ['Exit delay', maxExitDelay], ['Brokerage per order', brokerageFlat], ['Brokerage rate', brokerageRate], ['DP charge', dpCharge],
    ];
    const invalid = numericFields.find(([, value]) => value.trim() === '' || !Number.isFinite(Number(value)) || Number(value) < 0);
    if (invalid || Number(holdingSessions) < 1) {
      setRunError(`${invalid?.[0] ?? 'Holding sessions'} requires a valid non-negative value.`);
      return;
    }
    for (const parameter of strategy.parameters) {
      const value = parameters[parameter.code];
      if (parameter.value_type === 'DECIMAL' && (!String(value).trim() || !Number.isFinite(Number(value)))) {
        setRunError(`${parameter.display_name} requires a numeric value.`);
        return;
      }
    }
    const payload: BacktestRunRequest = {
      strategy_code: strategy.strategy_code,
      strategy_version: strategy.strategy_version,
      parameter_overrides: parameters,
      universe,
      start_date: startDate,
      end_date: endDate,
      adjustment_policy: adjustmentPolicy,
      profile_code: profile.profile_code,
      profile_version: profile.profile_version,
      holding_sessions: Number(holdingSessions),
      trade_notional_inr: notional,
      slippage_bps: slippage,
      stop_loss_pct: stopLoss.trim() ? String(Number(stopLoss) / 100) : null,
      profit_target_pct: profitTarget.trim() ? String(Number(profitTarget) / 100) : null,
      max_exit_delay_sessions: Number(maxExitDelay),
      cost_model_code: costModelCode,
      cost_model_version: costModelVersion,
      brokerage_per_order_inr: brokerageFlat,
      brokerage_rate: brokerageRate,
      dp_charge_per_scrip_sell_day_inr: dpCharge,
      out_of_sample_start_date: oosStart || null,
      trade_detail_limit: 500,
    };
    if ((payload.stop_loss_pct !== null && (!Number.isFinite(Number(payload.stop_loss_pct)) || Number(payload.stop_loss_pct) <= 0)) || (payload.profit_target_pct !== null && (!Number.isFinite(Number(payload.profit_target_pct)) || Number(payload.profit_target_pct) <= 0))) {
      setRunError('Optional stop and target values must be positive percentages.');
      return;
    }
    setRunning(true);
    setRunError(null);
    try {
      setResult(await api.runBacktest(payload));
    } catch (error) {
      setRunError(error instanceof Error ? error.message : 'The historical simulation could not be completed.');
    } finally {
      setRunning(false);
    }
  };

  return <TerminalShell title="Backtest research" eyebrow="Research">
    <PageHeader eyebrow="Point-in-time historical replay" title="Backtest research" description="Replay immutable strategy setups, then simulate independent next-open trades with explicit slippage and Indian cash-equity costs." meta={<span className="inline-flex items-center gap-2 border border-primary/25 bg-primary/[0.055] px-2.5 py-1.5 text-xs font-medium uppercase tracking-[0.08em] text-primary"><FlaskConical className="size-3.5" />Historical simulation</span>} />

    <Alert className="mb-4 rounded-none border-warning/25 bg-warning/[0.045] text-warning"><ShieldCheck /><AlertTitle>Historical simulation — not investment advice</AlertTitle><AlertDescription>Independent fixed-notional trade simulation. This is not a capital-constrained portfolio backtest and does not create recommendations or orders.</AlertDescription></Alert>

    {metadata.error ? <RequestError message={metadata.error} retry={metadata.retry} /> : <>
      <div className="grid gap-4 2xl:grid-cols-[1.45fr_.55fr]">
        <Surface>
          <SurfaceHeader eyebrow="Research definition" title="Strategy, universe, and period" description="Strategy rules remain the immutable Phase 4 definitions; execution assumptions live in the selected backtest profile." />
          <div className="grid gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3">
            <label htmlFor="backtest-strategy" className="grid gap-1.5 text-sm font-medium">Strategy
              {metadata.loading ? <Skeleton className="h-9 rounded-none" /> : <Select value={strategyCode} onValueChange={(value) => value && selectStrategy(value)}><SelectTrigger id="backtest-strategy" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectLabel>Immutable strategy registry</SelectLabel>{Array.from(new Map(metadata.data?.strategies.map((item) => [item.strategy_code, item]) ?? []).values()).map((item) => <SelectItem key={item.strategy_code} value={item.strategy_code}>{item.display_name}</SelectItem>)}</SelectGroup></SelectContent></Select>}
            </label>
            <label htmlFor="backtest-strategy-version" className="grid gap-1.5 text-sm font-medium">Strategy version
              <Select value={strategyVersion} onValueChange={(value) => value && setStrategyVersion(value)}><SelectTrigger id="backtest-strategy-version" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{strategiesForCode.map((item) => <SelectItem key={item.strategy_version} value={item.strategy_version}>Version {item.strategy_version}</SelectItem>)}</SelectContent></Select>
            </label>
            <label htmlFor="backtest-universe" className="grid gap-1.5 text-sm font-medium">Historical universe
              <Select value={universe} onValueChange={(value) => value && setUniverse(value)}><SelectTrigger id="backtest-universe" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{metadata.data?.universes.map((item) => <SelectItem key={item.id} value={item.symbol}>{item.name} · {item.symbol}</SelectItem>)}</SelectContent></Select>
            </label>
            <label htmlFor="backtest-start" className="grid gap-1.5 text-sm font-medium">Start date<Input id="backtest-start" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
            <label htmlFor="backtest-end" className="grid gap-1.5 text-sm font-medium">End date<Input id="backtest-end" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
            <label htmlFor="backtest-adjustment" className="grid gap-1.5 text-sm font-medium">Feature price policy<Select value={adjustmentPolicy} onValueChange={(value) => value && setAdjustmentPolicy(value as 'RAW' | 'ADJUSTED')}><SelectTrigger id="backtest-adjustment" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ADJUSTED">Adjusted features</SelectItem><SelectItem value="RAW">Raw features</SelectItem></SelectContent></Select></label>
          </div>
          <div className="border-t border-border/80 px-4 py-3"><p className="text-xs leading-5 text-muted-foreground">Features are evaluated after each NSE session close. Simulated execution always uses raw OHLC, beginning at the next session open.</p></div>
          <div className="grid gap-px border-t border-border/80 bg-border/70 sm:grid-cols-2 xl:grid-cols-3">
            {strategy?.parameters.map((parameter) => <label key={parameter.code} htmlFor={`backtest-param-${parameter.code}`} className="grid gap-1.5 bg-surface p-4 text-xs font-medium text-muted-foreground"><span className="text-foreground">{parameter.display_name}</span>{parameter.value_type === 'BOOLEAN' ? <Select value={String(parameters[parameter.code])} onValueChange={(value) => value && updateParameter(parameter, value)}><SelectTrigger id={`backtest-param-${parameter.code}`} className="h-9 w-full rounded-none bg-surface-inset text-foreground"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="true">True</SelectItem><SelectItem value="false">False</SelectItem></SelectContent></Select> : <Input id={`backtest-param-${parameter.code}`} inputMode="decimal" value={String(parameters[parameter.code] ?? '')} onChange={(event) => updateParameter(parameter, event.target.value)} className="h-9 rounded-none bg-surface-inset text-foreground" />}<span className="leading-4">{parameter.description}</span></label>)}
          </div>
        </Surface>

        <Surface>
          <SurfaceHeader eyebrow="Execution & costs" title="Versioned simulation assumptions" description="Daily RAW OHLC, adverse slippage, integer shares, and one open trade per security." />
          <div className="grid gap-3 p-4 sm:grid-cols-2 2xl:grid-cols-1">
            <label htmlFor="backtest-profile" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Backtest profile</span><Select value={`${profileCode}:${profileVersion}`} onValueChange={(value) => { const [code, version] = value?.split(':') ?? []; if (code && version) { setProfileCode(code); setProfileVersion(version); } }}><SelectTrigger id="backtest-profile" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{profiles.map((item) => <SelectItem key={`${item.profile_code}:${item.profile_version}`} value={`${item.profile_code}:${item.profile_version}`}>{item.profile_code} · v{item.profile_version}</SelectItem>)}</SelectContent></Select></label>
            <div className="grid grid-cols-2 gap-3"><label htmlFor="backtest-holding" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Holding sessions</span><Input id="backtest-holding" inputMode="numeric" value={holdingSessions} onChange={(event) => setHoldingSessions(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="backtest-notional" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Notional · INR</span><Input id="backtest-notional" inputMode="decimal" value={notional} onChange={(event) => setNotional(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
            <div className="grid grid-cols-2 gap-3"><label htmlFor="backtest-slippage" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Slippage · bps</span><Input id="backtest-slippage" inputMode="decimal" value={slippage} onChange={(event) => setSlippage(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="backtest-exit-delay" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Max exit delay</span><Input id="backtest-exit-delay" inputMode="numeric" value={maxExitDelay} onChange={(event) => setMaxExitDelay(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
            <div className="grid grid-cols-2 gap-3"><label htmlFor="backtest-stop" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Stop loss · % optional</span><Input id="backtest-stop" inputMode="decimal" placeholder="Disabled" value={stopLoss} onChange={(event) => setStopLoss(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="backtest-target" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Profit target · % optional</span><Input id="backtest-target" inputMode="decimal" placeholder="Disabled" value={profitTarget} onChange={(event) => setProfitTarget(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
            <label htmlFor="backtest-cost-model" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Cost model</span><Select value={`${costModelCode}:${costModelVersion}`} onValueChange={(value) => { const [code, version] = value?.split(':') ?? []; if (code && version) { setCostModelCode(code); setCostModelVersion(version); } }}><SelectTrigger id="backtest-cost-model" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{costModels.map((item) => <SelectItem key={`${item.code}:${item.version}`} value={`${item.code}:${item.version}`}>{item.code} · v{item.version}</SelectItem>)}</SelectContent></Select></label>
            <div className="grid grid-cols-3 gap-2"><label htmlFor="backtest-brokerage-flat" className="grid gap-1.5 text-[0.6875rem] font-medium text-muted-foreground"><span>Brokerage/order</span><Input id="backtest-brokerage-flat" inputMode="decimal" value={brokerageFlat} onChange={(event) => setBrokerageFlat(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="backtest-brokerage-rate" className="grid gap-1.5 text-[0.6875rem] font-medium text-muted-foreground"><span>Brokerage rate</span><Input id="backtest-brokerage-rate" inputMode="decimal" value={brokerageRate} onChange={(event) => setBrokerageRate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="backtest-dp" className="grid gap-1.5 text-[0.6875rem] font-medium text-muted-foreground"><span>DP sell charge</span><Input id="backtest-dp" inputMode="decimal" value={dpCharge} onChange={(event) => setDpCharge(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
            <label htmlFor="backtest-oos" className="grid gap-1.5 text-xs font-medium text-muted-foreground"><span className="text-foreground">Out-of-sample start · optional</span><Input id="backtest-oos" type="date" value={oosStart} onChange={(event) => setOosStart(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
            <Button onClick={runBacktest} disabled={running || metadata.loading} className="mt-1 w-full rounded-none"><Play className="size-4" />{running ? 'Running historical simulation…' : 'Run backtest'}</Button>
          </div>
        </Surface>
      </div>

      {runError && <Alert className="mt-4 rounded-none border-danger/30 bg-danger/[0.045] text-danger"><AlertTriangle /><AlertTitle>Backtest could not run</AlertTitle><AlertDescription>{runError}</AlertDescription></Alert>}
      {running && <Surface className="mt-4 p-4"><div className="grid gap-3 sm:grid-cols-4">{Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="h-24 rounded-none" />)}</div></Surface>}

      {result && !running && <div className="mt-4 grid gap-4">
        {result.warnings.length > 0 && <Alert className="rounded-none"><AlertTriangle /><AlertTitle>Research-quality notes</AlertTitle><AlertDescription>{result.warnings.map((warning) => warning.replaceAll('_', ' ')).join(' · ')}</AlertDescription></Alert>}

        <Surface><SurfaceHeader eyebrow="Full-run analytics" title={`${result.strategy.display_name} · ${formatDate(result.normalized_request.start_date)} to ${formatDate(result.normalized_request.end_date)}`} description={`${result.historical_sessions_processed} sessions · ${result.dataset.security_count} historical securities · ${result.timings.total_service_ms.toFixed(1)} ms service runtime`} /><div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-4"><MetricCard label="Matched setups" value={formatInteger.format(result.setup_count)} supporting={`${result.skipped_setup_count} skipped for execution`} icon={Target} accent /><MetricCard label="Executed trades" value={formatInteger.format(result.executed_trade_count)} supporting={`${result.analytics.closed_trade_count} with closed outcomes`} icon={Layers3} /><MetricCard label="Average net return" value={percentage(result.analytics.average_net_return)} supporting="After modeled costs" icon={TrendingUp} /><MetricCard label="Median net return" value={percentage(result.analytics.median_net_return)} supporting="Closed simulated trades" icon={BarChart3} /><MetricCard label="Win rate" value={percentage(result.analytics.win_rate)} supporting={`${result.analytics.winning_trades} winning · ${result.analytics.losing_trades} losing`} icon={Target} /><MetricCard label="Profit factor" value={decimal(result.analytics.profit_factor)} supporting="Net gains ÷ absolute net losses" icon={Calculator} /><MetricCard label="Total costs" value={currency(result.cost_analytics.total)} supporting={result.cost_analytics.broker_specific_costs_excluded ? 'Broker/DP costs excluded' : 'Includes configured broker costs'} icon={ReceiptText} /><MetricCard label="Cost drag" value={percentage(result.analytics.cost_drag_pct)} supporting="Costs ÷ deployed notional" icon={IndianRupee} /></div></Surface>

        {result.setup_count === 0 ? <NoResults message="No historical strategy setups matched this point-in-time period and universe." /> : result.executed_trade_count === 0 ? <NoResults message="Setups matched, but none could be executed under the selected next-open, overlap, price-availability, and notional rules." /> : <>
          <div className="grid gap-4 xl:grid-cols-[1.35fr_.65fr]">
            <Surface><SurfaceHeader eyebrow="Outcome distribution" title="Net return distribution" description={result.trades_truncated ? 'Returned trade preview only; summary cards use the full trade set.' : 'Closed independent fixed-notional simulated trades.'} /><div className="h-72 p-4"><ResponsiveContainer width="100%" height="100%"><BarChart data={distribution} margin={{ top: 8, right: 8, left: -20, bottom: 28 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="bucket" tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} angle={-28} textAnchor="end" interval={0} /><YAxis allowDecimals={false} tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} /><Tooltip cursor={{ fill: 'color-mix(in oklab, var(--primary) 8%, transparent)' }} contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 0, fontSize: 12 }} /><Bar dataKey="trades" name="Simulated trades" fill="var(--primary)" radius={0} /></BarChart></ResponsiveContainer></div></Surface>
            <Surface><SurfaceHeader eyebrow="Modeled costs" title="Aggregate charge breakdown" description={`${result.cost_model.code} · v${result.cost_model.version}`} /><div className="divide-y divide-border/75">{costRows(result.cost_analytics).map(([label, value]) => <div key={label} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm"><span className="text-muted-foreground">{label}</span><span className="numeric">{currency(value)}</span></div>)}<div className="flex items-center justify-between gap-4 bg-surface-inset px-4 py-3 text-sm font-semibold"><span>Total modeled costs</span><span className="numeric">{currency(result.cost_analytics.total)}</span></div></div>{result.cost_analytics.broker_specific_costs_excluded && <p className="border-t border-warning/20 bg-warning/[0.04] px-4 py-3 text-xs leading-5 text-warning">Brokerage and DP overrides are zero; broker-specific costs are excluded.</p>}</Surface>
          </div>

          <Surface><SurfaceHeader eyebrow="Historical results" title="Simulated trade preview" description={`${result.returned_trade_count} of ${result.total_trade_count} trades · deterministic entry-date and symbol ordering`} /><Table><TableHeader className="sticky top-0 bg-surface"><TableRow><TableHead>Symbol</TableHead><TableHead>Signal</TableHead><TableHead>Entry</TableHead><TableHead className="text-right">Entry price</TableHead><TableHead className="text-right">Qty</TableHead><TableHead>Exit</TableHead><TableHead className="text-right">Exit price</TableHead><TableHead>Reason</TableHead><TableHead className="text-right">Gross</TableHead><TableHead className="text-right">Net</TableHead><TableHead className="text-right">Net P&amp;L</TableHead><TableHead className="text-right">Costs</TableHead><TableHead className="text-right">Hold</TableHead><TableHead className="text-right">MAE</TableHead><TableHead className="text-right">MFE</TableHead><TableHead>Details</TableHead></TableRow></TableHeader><TableBody>{result.trades.map((trade) => <TableRow key={trade.trade_id}><TableCell><div className="font-semibold text-foreground">{trade.symbol}</div><div className="max-w-32 truncate text-[0.6875rem] text-muted-foreground">{trade.company_name}</div></TableCell><TableCell>{formatDate(trade.signal_date)}</TableCell><TableCell>{formatDate(trade.entry_date)}</TableCell><TableCell className="numeric text-right">{currency(trade.slipped_entry_price)}</TableCell><TableCell className="numeric text-right">{decimal(trade.quantity, 4)}</TableCell><TableCell>{formatDate(trade.exit_date)}</TableCell><TableCell className="numeric text-right">{currency(trade.slipped_exit_price)}</TableCell><TableCell><span className="border border-border bg-surface-inset px-1.5 py-1 text-[0.625rem] font-semibold uppercase tracking-[0.06em]">{trade.exit_reason.replaceAll('_', ' ')}</span></TableCell><TableCell className="numeric text-right">{percentage(trade.gross_return_pct)}</TableCell><TableCell className={`numeric text-right ${Number(trade.net_return_pct) > 0 ? 'text-bullish' : Number(trade.net_return_pct) < 0 ? 'text-bearish' : ''}`}>{percentage(trade.net_return_pct)}</TableCell><TableCell className="numeric text-right">{currency(trade.net_pnl)}</TableCell><TableCell className="numeric text-right">{currency(trade.total_costs)}</TableCell><TableCell className="numeric text-right">{trade.holding_sessions}</TableCell><TableCell className="numeric text-right text-bearish">{percentage(trade.maximum_adverse_excursion)}</TableCell><TableCell className="numeric text-right text-bullish">{percentage(trade.maximum_favorable_excursion)}</TableCell><TableCell><details className="w-64 text-xs"><summary className="focus-terminal cursor-pointer text-primary">Audit detail</summary><div className="mt-2 grid gap-2 whitespace-normal border border-border bg-surface-inset p-3 text-muted-foreground"><p><span className="text-foreground">Trade ID:</span> <span className="numeric break-all">{trade.trade_id}</span></p><p><span className="text-foreground">Strategy:</span> <span className="numeric break-all">{trade.strategy_fingerprint}</span></p><p><span className="text-foreground">Signal:</span> <span className="numeric break-all">{trade.signal_result_fingerprint}</span></p><p><span className="text-foreground">Trade:</span> <span className="numeric break-all">{trade.trade_fingerprint}</span></p><div><p className="mb-1 text-foreground">Entry charges</p>{costDetailRows(trade.entry_cost).map(([label, value]) => <span key={label} className="mr-2 inline-block">{label}: {currency(value)}</span>)}</div>{trade.exit_cost && <div><p className="mb-1 text-foreground">Exit charges</p>{costDetailRows(trade.exit_cost).map(([label, value]) => <span key={label} className="mr-2 inline-block">{label}: {currency(value)}</span>)}</div>}<p><span className="text-foreground">Corporate actions:</span> {trade.corporate_action_events.length ? trade.corporate_action_events.map((item) => `${item.action_type} ${item.ex_date}`).join(', ') : 'None'}</p><p><span className="text-foreground">Warnings:</span> {trade.warnings.length ? trade.warnings.join(', ') : 'None'}</p></div></details></TableCell></TableRow>)}</TableBody></Table></Surface>
        </>}

        <div className="grid gap-4 xl:grid-cols-2"><Surface><SurfaceHeader eyebrow="Stability" title="Yearly results" description="Trade-level results grouped by signal year with fixed parameters." /><PeriodTable rows={result.yearly_breakdown} /></Surface><Surface><SurfaceHeader eyebrow="Holdout" title="In-sample / out-of-sample" description={result.normalized_request.out_of_sample_start_date ? `Classified by signal date from ${formatDate(result.normalized_request.out_of_sample_start_date)}.` : 'Set an optional OOS start date before running to enable this view.'} />{result.normalized_request.out_of_sample_start_date ? <PeriodTable rows={result.holdout_breakdown} /> : <NoResults compact message="No out-of-sample boundary was requested for this run." />}</Surface></div>

        <Surface><SurfaceHeader eyebrow="Reproducibility" title="Run provenance" description="Equivalent definitions and eligible dataset state produce the same fingerprints." /><div className="grid gap-px bg-border/75 md:grid-cols-3">{[['Configuration', result.config_fingerprint], ['Dataset', result.dataset_fingerprint], ['Final run', result.run_fingerprint]].map(([label, value]) => <div key={label} className="min-w-0 bg-surface p-4"><p className="text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground">{label}</p><p className="numeric mt-2 break-all text-xs leading-5 text-foreground">{value}</p></div>)}</div><div className="grid gap-px border-t border-border/80 bg-border/75 sm:grid-cols-3"><div className="bg-surface px-4 py-3 text-xs text-muted-foreground"><Database className="mb-2 size-4 text-primary" />{result.dataset.dataset_code} · {result.dataset.dataset_version}</div><div className="bg-surface px-4 py-3 text-xs text-muted-foreground"><CalendarRange className="mb-2 size-4 text-primary" />{result.dataset.price_row_count.toLocaleString('en-IN')} projected price rows</div><div className="bg-surface px-4 py-3 text-xs text-muted-foreground"><ShieldCheck className="mb-2 size-4 text-primary" />Historical membership and action revisions included</div></div></Surface>
      </div>}
    </>}
  </TerminalShell>;
}
