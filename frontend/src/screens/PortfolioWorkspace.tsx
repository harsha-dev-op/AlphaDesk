'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Banknote, BarChart3, BriefcaseBusiness, CalendarRange, Database, Gauge, IndianRupee, Layers3, LineChart as LineChartIcon, Play, ReceiptText, ShieldCheck, Target, TrendingDown, TrendingUp, WalletCards } from 'lucide-react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
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
import type { BacktestCostBreakdown, PortfolioRunRequest, PortfolioRunResponse, StrategyMetadata, StrategyParameterMetadata, StrategyScalar } from '@/src/types/api';

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

function costRows(cost: PortfolioRunResponse['cost_analytics']) {
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

function costDetail(cost: BacktestCostBreakdown) {
  return [
    ['Turnover', cost.turnover], ['Brokerage', cost.brokerage], ['STT', cost.stt],
    ['Exchange', cost.exchange_transaction_charge], ['SEBI', cost.sebi_charge], ['GST', cost.gst],
    ['Stamp', cost.stamp_duty], ['DP', cost.dp_charge], ['Total', cost.total_charges],
  ];
}

export function PortfolioWorkspace() {
  const metadata = useApi(useCallback((signal: AbortSignal) => api.portfolioMetadata(signal), []));
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
  const [slippage, setSlippage] = useState('5');
  const [stopLoss, setStopLoss] = useState('');
  const [profitTarget, setProfitTarget] = useState('');
  const [maxExitDelay, setMaxExitDelay] = useState('5');
  const [costModelCode, setCostModelCode] = useState('');
  const [costModelVersion, setCostModelVersion] = useState('');
  const [brokerageFlat, setBrokerageFlat] = useState('0');
  const [brokerageRate, setBrokerageRate] = useState('0');
  const [dpCharge, setDpCharge] = useState('0');
  const [policyCode, setPolicyCode] = useState('');
  const [policyVersion, setPolicyVersion] = useState('');
  const [initialCapital, setInitialCapital] = useState('1000000');
  const [maxPositions, setMaxPositions] = useState('10');
  const [maxWeight, setMaxWeight] = useState('10');
  const [maxExposure, setMaxExposure] = useState('100');
  const [cashReserve, setCashReserve] = useState('0');
  const [riskFreeRate, setRiskFreeRate] = useState('0');
  const [oosStart, setOosStart] = useState('');
  const [result, setResult] = useState<PortfolioRunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const strategiesForCode = useMemo(() => metadata.data?.strategies.filter((item) => item.strategy_code === strategyCode) ?? [], [metadata.data, strategyCode]);
  const strategy = useMemo(() => strategiesForCode.find((item) => item.strategy_version === strategyVersion), [strategiesForCode, strategyVersion]);
  const profiles = useMemo(() => metadata.data?.profiles.filter((item) => item.compatible_strategies.includes(strategyCode)) ?? [], [metadata.data, strategyCode]);
  const profile = useMemo(() => profiles.find((item) => item.profile_code === profileCode && item.profile_version === profileVersion), [profiles, profileCode, profileVersion]);
  const policies = useMemo(() => metadata.data?.policies.filter((item) => item.compatible_profiles.includes(profileCode)) ?? [], [metadata.data, profileCode]);
  const policy = useMemo(() => policies.find((item) => item.policy_code === policyCode && item.policy_version === policyVersion), [policies, policyCode, policyVersion]);
  const costModels = metadata.data?.cost_models ?? [];
  const curve = useMemo(() => result?.daily_equity_curve.map((item) => ({ date: item.session_date, equity: Number(item.portfolio_equity), drawdown: Number(item.drawdown_pct) * 100, cash: Number(item.cash), exposure: Number(item.gross_exposure_pct) * 100, positions: item.open_position_count })) ?? [], [result]);

  useEffect(() => {
    if (!metadata.data || initialized.current) return;
    initialized.current = true;
    const selectedStrategy = metadata.data.strategies[0];
    const selectedProfile = metadata.data.profiles[0];
    const selectedCost = metadata.data.cost_models[0];
    const selectedPolicy = metadata.data.policies[0];
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
    setSlippage(selectedProfile?.default_slippage_bps ?? '5');
    setMaxExitDelay(String(selectedProfile?.default_max_exit_delay_sessions ?? 5));
    setCostModelCode(selectedCost?.code ?? '');
    setCostModelVersion(selectedCost?.version ?? '');
    setPolicyCode(selectedPolicy?.policy_code ?? '');
    setPolicyVersion(selectedPolicy?.policy_version ?? '');
    setInitialCapital(selectedPolicy?.default_initial_capital_inr ?? '1000000');
    setMaxPositions(String(selectedPolicy?.default_max_concurrent_positions ?? 10));
    setMaxWeight(String(Number(selectedPolicy?.default_max_position_weight ?? '0.10') * 100));
    setMaxExposure(String(Number(selectedPolicy?.default_max_gross_exposure ?? '1') * 100));
    setCashReserve(String(Number(selectedPolicy?.default_minimum_cash_reserve_pct ?? '0') * 100));
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

  const runPortfolio = async () => {
    if (!strategy || !profile || !policy || !universe || !startDate || !endDate || !costModelCode || !costModelVersion) {
      setRunError('Complete the strategy, universe, execution profile, portfolio policy, and dates before running.');
      return;
    }
    if (startDate > endDate) {
      setRunError('Start date must be on or before end date.');
      return;
    }
    const requiredNumbers = [holdingSessions, slippage, maxExitDelay, initialCapital, maxPositions, maxWeight, maxExposure, cashReserve, riskFreeRate];
    if (requiredNumbers.some((value) => value.trim() === '' || !Number.isFinite(Number(value)))) {
      setRunError('Portfolio and execution assumptions must contain valid numeric values.');
      return;
    }
    const payload: PortfolioRunRequest = {
      strategy_code: strategyCode,
      strategy_version: strategyVersion,
      parameter_overrides: parameters,
      universe,
      start_date: startDate,
      end_date: endDate,
      adjustment_policy: adjustmentPolicy,
      profile_code: profileCode,
      profile_version: profileVersion,
      holding_sessions: Number(holdingSessions),
      slippage_bps: slippage,
      stop_loss_pct: stopLoss ? String(Number(stopLoss) / 100) : null,
      profit_target_pct: profitTarget ? String(Number(profitTarget) / 100) : null,
      max_exit_delay_sessions: Number(maxExitDelay),
      cost_model_code: costModelCode,
      cost_model_version: costModelVersion,
      brokerage_per_order_inr: brokerageFlat,
      brokerage_rate: brokerageRate,
      dp_charge_per_scrip_sell_day_inr: dpCharge,
      portfolio_policy_code: policyCode,
      portfolio_policy_version: policyVersion,
      initial_capital_inr: initialCapital,
      max_concurrent_positions: Number(maxPositions),
      max_position_weight: String(Number(maxWeight) / 100),
      max_gross_exposure: String(Number(maxExposure) / 100),
      minimum_cash_reserve_pct: String(Number(cashReserve) / 100),
      risk_free_rate_annual: String(Number(riskFreeRate) / 100),
      out_of_sample_start_date: oosStart || null,
      position_detail_limit: 500,
      ledger_detail_limit: 1000,
    };
    setRunning(true);
    setRunError(null);
    setResult(null);
    try {
      setResult(await api.runPortfolio(payload));
    } catch (error) {
      setRunError(error instanceof Error ? error.message : 'Portfolio simulation failed.');
    } finally {
      setRunning(false);
    }
  };

  if (metadata.loading) return <TerminalShell title="Portfolio Research" eyebrow="Research"><div className="grid gap-4"><Skeleton className="h-28 rounded-none" /><div className="grid gap-4 xl:grid-cols-2"><Skeleton className="h-[34rem] rounded-none" /><Skeleton className="h-[34rem] rounded-none" /></div></div></TerminalShell>;
  if (metadata.error || !metadata.data) return <TerminalShell title="Portfolio Research" eyebrow="Research"><RequestError message={metadata.error ?? 'The portfolio research service did not return metadata.'} retry={metadata.retry} /></TerminalShell>;

  const metadataData = metadata.data;

  return <TerminalShell title="Portfolio Research" eyebrow="Research">
    <PageHeader eyebrow="Capital-aware historical simulation" title="Portfolio research" description="Replay immutable point-in-time setups through one finite cash pool, deterministic allocation, modeled costs, and daily mark-to-market risk accounting." meta={<span className="border border-primary/25 bg-primary/[0.06] px-2.5 py-1.5 text-[0.625rem] font-semibold uppercase tracking-[0.12em] text-primary">Historical simulation · not investment advice</span>} />
    <Alert className="mt-4 rounded-none border-primary/25 bg-primary/[0.035]"><ShieldCheck /><AlertTitle>One shared finite-capital portfolio</AlertTitle><AlertDescription>Entries compete for actual cash and capacity. There is no leverage, rebalancing, outcome ranking, broker connection, or order execution.</AlertDescription></Alert>

    <div className="mt-4 grid gap-4 2xl:grid-cols-[1.3fr_.7fr]">
      <Surface>
        <SurfaceHeader eyebrow="Research definition" title="Strategy, universe, and period" description="Phase 4 setup rules and Phase 5 next-open execution semantics remain authoritative." />
        <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
          <label htmlFor="portfolio-strategy" className="grid gap-1.5 text-sm font-medium">Strategy<Select value={strategyCode} onValueChange={(value) => value && selectStrategy(value)}><SelectTrigger id="portfolio-strategy" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{Array.from(new Set(metadataData.strategies.map((item) => item.strategy_code))).map((code) => <SelectItem key={code} value={code}>{metadataData.strategies.find((item) => item.strategy_code === code)?.display_name}</SelectItem>)}</SelectContent></Select></label>
          <label htmlFor="portfolio-strategy-version" className="grid gap-1.5 text-sm font-medium">Strategy version<Select value={strategyVersion} onValueChange={(value) => value && setStrategyVersion(value)}><SelectTrigger id="portfolio-strategy-version" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{strategiesForCode.map((item) => <SelectItem key={item.strategy_version} value={item.strategy_version}>v{item.strategy_version}</SelectItem>)}</SelectContent></Select></label>
          <label htmlFor="portfolio-universe" className="grid gap-1.5 text-sm font-medium">Historical universe<Select value={universe} onValueChange={(value) => value && setUniverse(value)}><SelectTrigger id="portfolio-universe" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{metadataData.universes.map((item) => <SelectItem key={item.id} value={item.symbol}>{item.name} · {item.symbol}</SelectItem>)}</SelectContent></Select></label>
          <label htmlFor="portfolio-start" className="grid gap-1.5 text-sm font-medium">Start date<Input id="portfolio-start" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
          <label htmlFor="portfolio-end" className="grid gap-1.5 text-sm font-medium">End date<Input id="portfolio-end" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label>
          <label htmlFor="portfolio-adjustment" className="grid gap-1.5 text-sm font-medium">Feature price policy<Select value={adjustmentPolicy} onValueChange={(value) => value && setAdjustmentPolicy(value as 'RAW' | 'ADJUSTED')}><SelectTrigger id="portfolio-adjustment" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ADJUSTED">Adjusted features</SelectItem><SelectItem value="RAW">Raw features</SelectItem></SelectContent></Select></label>
        </div>
        <div className="grid gap-px border-t border-border/80 bg-border/70 sm:grid-cols-2 xl:grid-cols-3">
          {strategy?.parameters.map((parameter) => <label key={parameter.code} htmlFor={`portfolio-param-${parameter.code}`} className="grid gap-1.5 bg-surface p-4 text-xs font-medium text-muted-foreground"><span className="text-foreground">{parameter.display_name}</span>{parameter.value_type === 'BOOLEAN' ? <Select value={String(parameters[parameter.code])} onValueChange={(value) => value && updateParameter(parameter, value)}><SelectTrigger id={`portfolio-param-${parameter.code}`} className="h-9 w-full rounded-none bg-surface-inset text-foreground"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="true">True</SelectItem><SelectItem value="false">False</SelectItem></SelectContent></Select> : <Input id={`portfolio-param-${parameter.code}`} inputMode="decimal" value={String(parameters[parameter.code] ?? '')} onChange={(event) => updateParameter(parameter, event.target.value)} className="h-9 rounded-none bg-surface-inset text-foreground" />}<span className="leading-4">{parameter.description}</span></label>)}
        </div>
      </Surface>

      <div className="grid gap-4">
        <Surface><SurfaceHeader eyebrow="Execution" title="Phase 5 profile and costs" description="RAW tradable prices, adverse slippage, exact delivery charges." /><div className="grid gap-3 p-4 sm:grid-cols-2 2xl:grid-cols-1">
          <label htmlFor="portfolio-profile" className="grid gap-1.5 text-xs font-medium">Backtest profile<Select value={`${profileCode}:${profileVersion}`} onValueChange={(value) => { const [code, version] = value?.split(':') ?? []; if (code && version) { setProfileCode(code); setProfileVersion(version); } }}><SelectTrigger id="portfolio-profile" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{profiles.map((item) => <SelectItem key={`${item.profile_code}:${item.profile_version}`} value={`${item.profile_code}:${item.profile_version}`}>{item.profile_code} · v{item.profile_version}</SelectItem>)}</SelectContent></Select></label>
          <div className="grid grid-cols-2 gap-3"><label htmlFor="portfolio-holding" className="grid gap-1.5 text-xs font-medium">Holding sessions<Input id="portfolio-holding" value={holdingSessions} onChange={(event) => setHoldingSessions(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-slippage" className="grid gap-1.5 text-xs font-medium">Slippage · bps<Input id="portfolio-slippage" value={slippage} onChange={(event) => setSlippage(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
          <div className="grid grid-cols-3 gap-2"><label htmlFor="portfolio-stop" className="grid gap-1.5 text-xs font-medium">Stop · %<Input id="portfolio-stop" placeholder="Disabled" value={stopLoss} onChange={(event) => setStopLoss(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-target" className="grid gap-1.5 text-xs font-medium">Target · %<Input id="portfolio-target" placeholder="Disabled" value={profitTarget} onChange={(event) => setProfitTarget(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-delay" className="grid gap-1.5 text-xs font-medium">Exit delay<Input id="portfolio-delay" value={maxExitDelay} onChange={(event) => setMaxExitDelay(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
          <label htmlFor="portfolio-cost" className="grid gap-1.5 text-xs font-medium">Cost model<Select value={`${costModelCode}:${costModelVersion}`} onValueChange={(value) => { const [code, version] = value?.split(':') ?? []; if (code && version) { setCostModelCode(code); setCostModelVersion(version); } }}><SelectTrigger id="portfolio-cost" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{costModels.map((item) => <SelectItem key={`${item.code}:${item.version}`} value={`${item.code}:${item.version}`}>{item.code} · v{item.version}</SelectItem>)}</SelectContent></Select></label>
          <div className="grid grid-cols-3 gap-2"><label htmlFor="portfolio-brokerage" className="grid gap-1.5 text-[0.6875rem] font-medium">Brokerage/order<Input id="portfolio-brokerage" value={brokerageFlat} onChange={(event) => setBrokerageFlat(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-brokerage-rate" className="grid gap-1.5 text-[0.6875rem] font-medium">Brokerage rate<Input id="portfolio-brokerage-rate" value={brokerageRate} onChange={(event) => setBrokerageRate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-dp" className="grid gap-1.5 text-[0.6875rem] font-medium">DP sell charge<Input id="portfolio-dp" value={dpCharge} onChange={(event) => setDpCharge(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
        </div></Surface>

        <Surface><SurfaceHeader eyebrow="Capital policy" title="Shared cash and allocation" description="Equal slots, deterministic symbol order, integer affordability including costs." /><div className="grid gap-3 p-4 sm:grid-cols-2 2xl:grid-cols-1">
          <label htmlFor="portfolio-policy" className="grid gap-1.5 text-xs font-medium">Portfolio policy<Select value={`${policyCode}:${policyVersion}`} onValueChange={(value) => { const [code, version] = value?.split(':') ?? []; if (code && version) { setPolicyCode(code); setPolicyVersion(version); } }}><SelectTrigger id="portfolio-policy" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{policies.map((item) => <SelectItem key={`${item.policy_code}:${item.policy_version}`} value={`${item.policy_code}:${item.policy_version}`}>{item.display_name} · v{item.policy_version}</SelectItem>)}</SelectContent></Select></label>
          <div className="grid grid-cols-2 gap-3"><label htmlFor="portfolio-capital" className="grid gap-1.5 text-xs font-medium">Initial capital · INR<Input id="portfolio-capital" value={initialCapital} onChange={(event) => setInitialCapital(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-max-positions" className="grid gap-1.5 text-xs font-medium">Maximum positions<Input id="portfolio-max-positions" value={maxPositions} onChange={(event) => setMaxPositions(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
          <div className="grid grid-cols-3 gap-2"><label htmlFor="portfolio-max-weight" className="grid gap-1.5 text-[0.6875rem] font-medium">Max weight · %<Input id="portfolio-max-weight" value={maxWeight} onChange={(event) => setMaxWeight(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-max-exposure" className="grid gap-1.5 text-[0.6875rem] font-medium">Gross exposure · %<Input id="portfolio-max-exposure" value={maxExposure} onChange={(event) => setMaxExposure(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-reserve" className="grid gap-1.5 text-[0.6875rem] font-medium">Cash reserve · %<Input id="portfolio-reserve" value={cashReserve} onChange={(event) => setCashReserve(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
          <div className="grid grid-cols-2 gap-3"><label htmlFor="portfolio-risk-free" className="grid gap-1.5 text-xs font-medium">Risk-free rate · annual %<Input id="portfolio-risk-free" value={riskFreeRate} onChange={(event) => setRiskFreeRate(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="portfolio-oos" className="grid gap-1.5 text-xs font-medium">OOS start · optional<Input id="portfolio-oos" type="date" value={oosStart} onChange={(event) => setOosStart(event.target.value)} className="h-9 rounded-none bg-surface-inset" /></label></div>
          <Button onClick={runPortfolio} disabled={running} className="mt-1 w-full rounded-none"><Play className="size-4" />{running ? 'Running portfolio simulation…' : 'Run portfolio simulation'}</Button>
        </div></Surface>
      </div>
    </div>

    {runError && <Alert className="mt-4 rounded-none border-danger/30 bg-danger/[0.045] text-danger"><AlertTriangle /><AlertTitle>Portfolio simulation could not run</AlertTitle><AlertDescription>{runError}</AlertDescription></Alert>}
    {running && <Surface className="mt-4 p-4"><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 12 }, (_, index) => <Skeleton key={index} className="h-24 rounded-none" />)}</div></Surface>}

    {result && !running && <div className="mt-4 grid gap-4">
      {result.warnings.length > 0 && <Alert className="rounded-none"><AlertTriangle /><AlertTitle>Research-quality notes</AlertTitle><AlertDescription>{result.warnings.map((warning) => warning.replaceAll('_', ' ')).join(' · ')}</AlertDescription></Alert>}

      <Surface><SurfaceHeader eyebrow="True portfolio analytics" title={`${result.strategy.display_name} · ${formatDate(result.normalized_request.start_date)} to ${formatDate(result.normalized_request.end_date)}`} description={`${result.historical_sessions_processed} sessions · ${result.dataset.security_count} historical securities · one shared ₹${formatPrice.format(Number(result.metrics.initial_capital))} cash ledger`} /><div className="grid gap-px bg-border/70 sm:grid-cols-2 xl:grid-cols-4"><MetricCard label="Initial capital" value={currency(result.metrics.initial_capital)} supporting="One finite cash pool" icon={WalletCards} accent /><MetricCard label="Ending equity" value={currency(result.metrics.ending_equity)} supporting={`${currency(result.metrics.net_portfolio_pnl)} net P&L`} icon={IndianRupee} /><MetricCard label="Total return" value={percentage(result.metrics.total_portfolio_return)} supporting="After modeled costs" icon={TrendingUp} /><MetricCard label="CAGR" value={percentage(result.metrics.cagr)} supporting="Actual elapsed calendar days" icon={CalendarRange} /><MetricCard label="Annualized volatility" value={percentage(result.metrics.annualized_volatility)} supporting="Sample daily-return volatility" icon={BarChart3} /><MetricCard label="Sharpe" value={decimal(result.metrics.sharpe_ratio)} supporting="Configured annual risk-free rate" icon={Gauge} /><MetricCard label="Sortino" value={decimal(result.metrics.sortino_ratio)} supporting="Downside daily excess deviation" icon={TrendingUp} /><MetricCard label="Maximum drawdown" value={percentage(result.metrics.drawdown.max_drawdown_pct)} supporting={currency(result.metrics.drawdown.max_drawdown_inr)} icon={TrendingDown} /><MetricCard label="Calmar" value={decimal(result.metrics.calmar_ratio)} supporting="CAGR ÷ absolute drawdown" icon={LineChartIcon} /><MetricCard label="Average exposure" value={percentage(result.metrics.average_gross_exposure)} supporting={`Max ${percentage(result.metrics.maximum_gross_exposure)}`} icon={BriefcaseBusiness} /><MetricCard label="Average cash" value={percentage(result.metrics.average_cash_pct)} supporting={`Minimum ${currency(result.metrics.minimum_cash)}`} icon={Banknote} /><MetricCard label="Total costs" value={currency(result.metrics.total_modeled_transaction_costs)} supporting={`${percentage(result.metrics.cost_drag)} of traded turnover`} icon={ReceiptText} /></div></Surface>

      {result.setup_count === 0 ? <NoResults message="No historical strategy setups matched this point-in-time period and universe." /> : result.accepted_entry_count === 0 ? <NoResults message="Setups matched, but finite cash, capacity, exposure, price availability, or integer affordability prevented allocation." /> : <>
        <div className="grid gap-4 xl:grid-cols-2">
          <Surface><SurfaceHeader eyebrow="Portfolio equity" title="Daily closing portfolio equity" description="Cash plus RAW-close marked open positions, after all session events and modeled costs." /><div className="h-72 min-w-0 p-4"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0} initialDimension={{ width: 320, height: 240 }}><LineChart data={curve} margin={{ top: 8, right: 14, left: 16, bottom: 20 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} minTickGap={36} /><YAxis tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} domain={['auto', 'auto']} width={72} /><Tooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 0, fontSize: 12 }} /><Line type="monotone" dataKey="equity" name="Portfolio equity" stroke="var(--primary)" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div></Surface>
          <Surface><SurfaceHeader eyebrow="Drawdown" title="Daily portfolio drawdown" description={`Peak ${formatDate(result.metrics.drawdown.peak_date)} · trough ${formatDate(result.metrics.drawdown.trough_date)} · recovery ${formatDate(result.metrics.drawdown.recovery_date)}`} /><div className="h-72 min-w-0 p-4"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0} initialDimension={{ width: 320, height: 240 }}><LineChart data={curve} margin={{ top: 8, right: 14, left: 4, bottom: 20 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} minTickGap={36} /><YAxis tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} unit="%" /><Tooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 0, fontSize: 12 }} /><Line type="monotone" dataKey="drawdown" name="Drawdown %" stroke="var(--danger)" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div><p className="border-t border-border px-4 py-3 text-xs text-muted-foreground">Duration: {result.metrics.drawdown.duration_sessions ?? '—'} sessions</p></Surface>
        </div>

        <div className="grid gap-4 xl:grid-cols-[1.3fr_.7fr]">
          <Surface><SurfaceHeader eyebrow="Capital state" title="Cash and gross exposure" description={`Average ${decimal(result.metrics.average_open_positions)} open positions · maximum ${result.metrics.maximum_open_positions}`} /><div className="h-72 min-w-0 p-4"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0} initialDimension={{ width: 320, height: 240 }}><LineChart data={curve} margin={{ top: 8, right: 14, left: 4, bottom: 20 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} minTickGap={36} /><YAxis tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }} unit="%" /><Tooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 0, fontSize: 12 }} /><Line type="monotone" dataKey="exposure" name="Gross exposure %" stroke="var(--primary)" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="positions" name="Open positions" stroke="var(--warning)" strokeWidth={1.5} dot={false} /></LineChart></ResponsiveContainer></div></Surface>
          <Surface><SurfaceHeader eyebrow="Modeled costs" title="Aggregate charge breakdown" description={`${result.cost_model.code} · v${result.cost_model.version}`} /><div className="divide-y divide-border/75">{costRows(result.cost_analytics).map(([label, value]) => <div key={label} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm"><span className="text-muted-foreground">{label}</span><span className="numeric">{currency(value)}</span></div>)}<div className="flex items-center justify-between gap-4 bg-surface-inset px-4 py-3 text-sm font-semibold"><span>Total modeled costs</span><span className="numeric">{currency(result.cost_analytics.total)}</span></div></div>{result.cost_analytics.broker_specific_costs_excluded && <p className="border-t border-warning/20 bg-warning/[0.04] px-4 py-3 text-xs leading-5 text-warning">Brokerage and DP overrides are zero; broker-specific costs are excluded.</p>}</Surface>
        </div>

        <Surface><SurfaceHeader eyebrow="Allocation history" title="Simulated portfolio positions" description={`${result.returned_position_count} of ${result.total_position_count} positions · deterministic entry-date and symbol ordering`} /><Table><TableHeader><TableRow><TableHead>Symbol</TableHead><TableHead>Signal</TableHead><TableHead>Entry</TableHead><TableHead className="text-right">Qty</TableHead><TableHead className="text-right">Entry price</TableHead><TableHead className="text-right">Capital</TableHead><TableHead className="text-right">Weight</TableHead><TableHead>Exit</TableHead><TableHead>Reason</TableHead><TableHead className="text-right">Exit price</TableHead><TableHead className="text-right">Net P&amp;L</TableHead><TableHead className="text-right">Net return</TableHead><TableHead className="text-right">Costs</TableHead><TableHead className="text-right">Contribution</TableHead><TableHead>Details</TableHead></TableRow></TableHeader><TableBody>{result.positions.map((position) => <TableRow key={position.position_id}><TableCell><div className="font-semibold">{position.symbol}</div><div className="max-w-32 truncate text-[0.6875rem] text-muted-foreground">{position.company_name}</div></TableCell><TableCell>{formatDate(position.signal_date)}</TableCell><TableCell>{formatDate(position.entry_date)}</TableCell><TableCell className="numeric text-right">{decimal(position.current_quantity, 4)}</TableCell><TableCell className="numeric text-right">{currency(position.entry_slipped_price)}</TableCell><TableCell className="numeric text-right">{currency(position.entry_turnover)}</TableCell><TableCell className="numeric text-right">{percentage(position.entry_portfolio_weight)}</TableCell><TableCell>{formatDate(position.exit_date)}</TableCell><TableCell><span className="border border-border bg-surface-inset px-1.5 py-1 text-[0.625rem] font-semibold uppercase tracking-[0.05em]">{position.exit_reason.replaceAll('_', ' ')}</span></TableCell><TableCell className="numeric text-right">{currency(position.exit_slipped_price)}</TableCell><TableCell className={`numeric text-right ${Number(position.net_pnl) > 0 ? 'text-bullish' : Number(position.net_pnl) < 0 ? 'text-bearish' : ''}`}>{currency(position.net_pnl)}</TableCell><TableCell className="numeric text-right">{percentage(position.net_return)}</TableCell><TableCell className="numeric text-right">{currency(String(Number(position.entry_costs.total_charges) + Number(position.exit_costs?.total_charges ?? 0)))}</TableCell><TableCell className="numeric text-right">{percentage(position.portfolio_contribution)}</TableCell><TableCell><details className="w-72 text-xs"><summary className="focus-terminal cursor-pointer text-primary">Allocation audit</summary><div className="mt-2 grid gap-2 whitespace-normal border border-border bg-surface-inset p-3 text-muted-foreground"><p><span className="text-foreground">Position:</span> <span className="numeric break-all">{position.position_id}</span></p><p><span className="text-foreground">Strategy:</span> <span className="numeric break-all">{position.strategy_fingerprint}</span></p><p><span className="text-foreground">Signal:</span> <span className="numeric break-all">{position.signal_fingerprint}</span></p><p><span className="text-foreground">Position fingerprint:</span> <span className="numeric break-all">{position.position_fingerprint}</span></p><div><p className="mb-1 text-foreground">Entry charges</p>{costDetail(position.entry_costs).map(([label, value]) => <span key={label} className="mr-2 inline-block">{label}: {currency(value)}</span>)}</div>{position.exit_costs && <div><p className="mb-1 text-foreground">Exit charges</p>{costDetail(position.exit_costs).map(([label, value]) => <span key={label} className="mr-2 inline-block">{label}: {currency(value)}</span>)}</div>}<p><span className="text-foreground">Corporate actions:</span> {position.corporate_action_events.length ? position.corporate_action_events.map((item) => `${item.action_type} ${item.ex_date}`).join(', ') : 'None'}</p><p><span className="text-foreground">Warnings:</span> {position.warnings.length ? position.warnings.join(', ') : 'None'}</p></div></details></TableCell></TableRow>)}</TableBody></Table></Surface>
      </>}

      <div className="grid gap-4 xl:grid-cols-2">
        <Surface><SurfaceHeader eyebrow="Rejected candidates" title="Allocation transparency" description={`${result.rejected_candidate_count} setups rejected by deterministic portfolio constraints`} />{Object.keys(result.rejected_candidate_reasons).length ? <div className="divide-y divide-border/75">{Object.entries(result.rejected_candidate_reasons).map(([reason, count]) => <div key={reason} className="flex items-center justify-between gap-3 px-4 py-3 text-sm"><span className="text-muted-foreground">{reason.replaceAll('_', ' ')}</span><span className="numeric font-semibold">{formatInteger.format(count)}</span></div>)}</div> : <NoResults compact message="No portfolio candidates were rejected." />}</Surface>
        <Surface><SurfaceHeader eyebrow="Holdout" title="Continuous in-sample / out-of-sample" description={result.normalized_request.out_of_sample_start_date ? `Reporting boundary ${formatDate(result.normalized_request.out_of_sample_start_date)}; cash and positions are not reset.` : 'Set an optional OOS boundary before running to enable this view.'} />{result.oos_metrics.length ? <Table><TableHeader><TableRow><TableHead>Segment</TableHead><TableHead className="text-right">Start</TableHead><TableHead className="text-right">End</TableHead><TableHead className="text-right">Return</TableHead><TableHead className="text-right">Sharpe</TableHead><TableHead className="text-right">Drawdown</TableHead></TableRow></TableHeader><TableBody>{result.oos_metrics.map((segment) => <TableRow key={segment.label}><TableCell className="font-semibold">{segment.label.replaceAll('_', ' ')}</TableCell><TableCell className="numeric text-right">{currency(segment.starting_equity)}</TableCell><TableCell className="numeric text-right">{currency(segment.ending_equity)}</TableCell><TableCell className="numeric text-right">{percentage(segment.total_return)}</TableCell><TableCell className="numeric text-right">{decimal(segment.sharpe_ratio)}</TableCell><TableCell className="numeric text-right">{percentage(segment.max_drawdown_pct)}</TableCell></TableRow>)}</TableBody></Table> : <NoResults compact message="No out-of-sample boundary was requested." />}</Surface>
      </div>

      <Surface><SurfaceHeader eyebrow="Cash ledger" title="Deterministic capital events" description={`${result.returned_ledger_event_count} of ${result.total_ledger_event_count} events · principal and costs recorded separately`} /><Table><TableHeader><TableRow><TableHead>Seq</TableHead><TableHead>Date</TableHead><TableHead>Symbol</TableHead><TableHead>Event</TableHead><TableHead className="text-right">Gross</TableHead><TableHead className="text-right">Costs</TableHead><TableHead className="text-right">Cash change</TableHead><TableHead className="text-right">Cash balance</TableHead></TableRow></TableHeader><TableBody>{result.ledger_events.map((item) => <TableRow key={item.ledger_event_id}><TableCell className="numeric">{item.sequence}</TableCell><TableCell>{formatDate(item.session_date)}</TableCell><TableCell className="font-semibold">{item.symbol ?? 'PORTFOLIO'}</TableCell><TableCell>{item.event_type.replaceAll('_', ' ')}</TableCell><TableCell className="numeric text-right">{currency(item.gross_amount)}</TableCell><TableCell className="numeric text-right">{currency(item.cost_amount)}</TableCell><TableCell className="numeric text-right">{currency(item.net_cash_change)}</TableCell><TableCell className="numeric text-right">{currency(item.resulting_cash_balance)}</TableCell></TableRow>)}</TableBody></Table></Surface>

      <Surface><SurfaceHeader eyebrow="Reproducibility" title="Portfolio run provenance" description="Configuration, point-in-time dataset, accepted/rejected events, ledger, and daily equity are fingerprinted." /><div className="grid gap-px bg-border/75 md:grid-cols-3">{[['Configuration', result.config_fingerprint], ['Dataset', result.dataset_fingerprint], ['Final run', result.run_fingerprint]].map(([label, value]) => <div key={label} className="min-w-0 bg-surface p-4"><p className="text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground">{label}</p><p className="numeric mt-2 break-all text-xs leading-5">{value}</p></div>)}</div><div className="grid gap-px border-t border-border/80 bg-border/75 sm:grid-cols-3"><div className="bg-surface px-4 py-3 text-xs text-muted-foreground"><Database className="mb-2 size-4 text-primary" />{result.dataset.dataset_code} · {result.dataset.dataset_version}</div><div className="bg-surface px-4 py-3 text-xs text-muted-foreground"><Layers3 className="mb-2 size-4 text-primary" />{result.setup_count} setups · {result.accepted_entry_count} allocated</div><div className="bg-surface px-4 py-3 text-xs text-muted-foreground"><Target className="mb-2 size-4 text-primary" />{result.portfolio_policy.candidate_selection_policy.replaceAll('_', ' ')}</div></div></Surface>
    </div>}
  </TerminalShell>;
}
