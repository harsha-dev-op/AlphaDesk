'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Beaker, Check, Clock3, Database, FlaskConical, Play, ShieldCheck, Users, X } from 'lucide-react';
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
import { formatDate, formatDateTime, formatInteger, formatPrice } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { FeatureValue, StrategyEvaluationRequest, StrategyEvaluationResponse, StrategyMetadata, StrategyParameterMetadata, StrategyScalar } from '@/src/types/api';

function istTimestamp(value: string): string {
  return `${value.length === 16 ? `${value}:00` : value}+05:30`;
}

function displayValue(value: FeatureValue | StrategyScalar, unit?: string): string {
  if (value === null) return 'Unavailable';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  if (unit === 'DECIMAL_FRACTION') return `${(numeric * 100).toFixed(2)}%`;
  if (unit === 'PRICE' || unit === 'CURRENCY') return formatPrice.format(numeric);
  if (unit === 'SHARES') return formatInteger.format(numeric);
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 4 }).format(numeric);
}

function defaultsFor(strategy?: StrategyMetadata): Record<string, StrategyScalar> {
  return Object.fromEntries(strategy?.parameters.map((parameter) => [parameter.code, parameter.default_value]) ?? []);
}

function isStale(latestObservation: string | null | undefined): boolean {
  if (!latestObservation) return false;
  const age = Date.now() - new Date(`${latestObservation}T00:00:00Z`).getTime();
  return age > 7 * 24 * 60 * 60 * 1_000;
}

export function StrategiesWorkspace() {
  const catalog = useApi(useCallback((signal: AbortSignal) => api.strategyCatalog(signal), []));
  const initialized = useRef(false);
  const [strategyCode, setStrategyCode] = useState('');
  const [strategyVersion, setStrategyVersion] = useState('');
  const [universe, setUniverse] = useState('');
  const [observationDate, setObservationDate] = useState('');
  const [asOf, setAsOf] = useState('');
  const [adjustmentPolicy, setAdjustmentPolicy] = useState<'RAW' | 'ADJUSTED'>('ADJUSTED');
  const [parameterValues, setParameterValues] = useState<Record<string, StrategyScalar>>({});
  const [evaluation, setEvaluation] = useState<StrategyEvaluationResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);

  const strategiesForCode = useMemo(
    () => catalog.data?.strategies.filter((strategy) => strategy.strategy_code === strategyCode) ?? [],
    [catalog.data, strategyCode],
  );
  const strategy = useMemo(
    () => strategiesForCode.find((item) => item.strategy_version === strategyVersion),
    [strategiesForCode, strategyVersion],
  );
  const ruleMap = useMemo(
    () => new Map(strategy?.rules.map((rule) => [rule.feature_code, rule]) ?? []),
    [strategy],
  );

  useEffect(() => {
    if (!catalog.data || initialized.current) return;
    initialized.current = true;
    const first = catalog.data.strategies[0];
    const date = catalog.data.latest_observation_date ?? new Date().toISOString().slice(0, 10);
    setStrategyCode(first?.strategy_code ?? '');
    setStrategyVersion(first?.strategy_version ?? '');
    setParameterValues(defaultsFor(first));
    setUniverse(catalog.data.universes[0]?.symbol ?? '');
    setObservationDate(date);
    setAsOf(`${date}T15:30`);
  }, [catalog.data]);

  const selectStrategyCode = (code: string) => {
    const selected = catalog.data?.strategies.find((item) => item.strategy_code === code);
    setStrategyCode(code);
    setStrategyVersion(selected?.strategy_version ?? '');
    setParameterValues(defaultsFor(selected));
    setEvaluation(null);
  };

  const selectStrategyVersion = (version: string) => {
    const selected = strategiesForCode.find((item) => item.strategy_version === version);
    setStrategyVersion(version);
    setParameterValues(defaultsFor(selected));
    setEvaluation(null);
  };

  const updateParameter = (parameter: StrategyParameterMetadata, value: string) => {
    setParameterValues((current) => ({
      ...current,
      [parameter.code]: parameter.value_type === 'BOOLEAN' ? value === 'true' : value,
    }));
  };

  const runEvaluation = async () => {
    if (!strategy || !universe || !observationDate || !asOf) {
      setEvaluationError('Complete the strategy, universe, date, and as-of fields before evaluating.');
      return;
    }
    for (const parameter of strategy.parameters) {
      const value = parameterValues[parameter.code];
      if (parameter.value_type === 'DECIMAL') {
        const numeric = Number(value);
        if (!String(value).trim() || !Number.isFinite(numeric)) {
          setEvaluationError(`${parameter.display_name} requires a numeric value.`);
          return;
        }
        if (parameter.minimum !== null && numeric < Number(parameter.minimum)) {
          setEvaluationError(`${parameter.display_name} must be at least ${parameter.minimum}.`);
          return;
        }
        if (parameter.maximum !== null && numeric > Number(parameter.maximum)) {
          setEvaluationError(`${parameter.display_name} must be at most ${parameter.maximum}.`);
          return;
        }
      }
    }
    const payload: StrategyEvaluationRequest = {
      strategy_code: strategy.strategy_code,
      strategy_version: strategy.strategy_version,
      universe,
      observation_date: observationDate,
      as_of: istTimestamp(asOf),
      adjustment_policy: adjustmentPolicy,
      parameter_overrides: parameterValues,
    };
    setRunning(true);
    setEvaluationError(null);
    try {
      setEvaluation(await api.evaluateStrategy(payload));
    } catch (error) {
      setEvaluationError(error instanceof Error ? error.message : 'The strategy evaluation could not be completed.');
    } finally {
      setRunning(false);
    }
  };

  return <TerminalShell title="Strategy research" eyebrow="Research">
    <PageHeader
      eyebrow="Deterministic setup detection"
      title="Strategy research"
      description="Evaluate versioned research hypotheses over historical universe membership using point-in-time technical features."
      meta={<span className="inline-flex items-center gap-2 border border-primary/25 bg-primary/[0.055] px-2.5 py-1.5 text-xs font-medium text-primary"><FlaskConical className="size-3.5" />Research only</span>}
    />

    <Alert className="mb-4 rounded-none border-warning/25 bg-warning/[0.045] text-warning"><ShieldCheck /><AlertTitle>Research setups — not investment recommendations</AlertTitle><AlertDescription>No ranking, execution, expected-return estimate, or brokerage connection is used in this workspace.</AlertDescription></Alert>

    {catalog.error ? <RequestError message={catalog.error} retry={catalog.retry} /> : <>
      {isStale(catalog.data?.latest_observation_date) && <Alert className="mb-4 rounded-none"><Database /><AlertTitle>Demo data may be stale</AlertTitle><AlertDescription>The latest local observation is {formatDate(catalog.data?.latest_observation_date ?? '')}. Confirm the research clock before interpreting setup results.</AlertDescription></Alert>}

      <div className="grid gap-4 xl:grid-cols-[1.55fr_.45fr]">
        <Surface>
          <SurfaceHeader eyebrow="Evaluation definition" title="Strategy, universe, and research clock" description="The registry supplies rule and parameter metadata; the selected as-of time enforces EOD availability." />
          <div className="grid gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3">
            <label htmlFor="strategy-code" className="grid gap-1.5 text-sm font-medium">Strategy
              {catalog.loading ? <Skeleton className="h-9 w-full rounded-none" /> : <Select value={strategyCode} onValueChange={(value) => value && selectStrategyCode(value)}><SelectTrigger id="strategy-code" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent align="start"><SelectGroup><SelectLabel>Versioned research definitions</SelectLabel>{Array.from(new Map(catalog.data?.strategies.map((item) => [item.strategy_code, item]) ?? []).values()).map((item) => <SelectItem key={item.strategy_code} value={item.strategy_code}>{item.display_name}</SelectItem>)}</SelectGroup></SelectContent></Select>}
            </label>
            <label htmlFor="strategy-version" className="grid gap-1.5 text-sm font-medium">Version
              <Select value={strategyVersion} onValueChange={(value) => value && selectStrategyVersion(value)} disabled={!strategiesForCode.length}><SelectTrigger id="strategy-version" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{strategiesForCode.map((item) => <SelectItem key={item.strategy_version} value={item.strategy_version}>Version {item.strategy_version}</SelectItem>)}</SelectContent></Select>
            </label>
            <label htmlFor="strategy-universe" className="grid gap-1.5 text-sm font-medium">Universe
              <Select value={universe} onValueChange={(value) => value && setUniverse(value)}><SelectTrigger id="strategy-universe" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent align="start">{catalog.data?.universes.map((item) => <SelectItem key={item.id} value={item.symbol}>{item.name} · {item.symbol}</SelectItem>)}</SelectContent></Select>
            </label>
            <label htmlFor="strategy-date" className="grid gap-1.5 text-sm font-medium">Observation date
              <Input id="strategy-date" type="date" value={observationDate} onChange={(event) => { setObservationDate(event.target.value); setAsOf(`${event.target.value}T15:30`); }} className="h-9 rounded-none bg-surface-inset" />
            </label>
            <label htmlFor="strategy-as-of" className="grid gap-1.5 text-sm font-medium">As of · IST
              <Input id="strategy-as-of" type="datetime-local" value={asOf} onChange={(event) => setAsOf(event.target.value)} className="h-9 rounded-none bg-surface-inset" />
            </label>
            <label htmlFor="strategy-policy" className="grid gap-1.5 text-sm font-medium">Price policy
              <Select value={adjustmentPolicy} onValueChange={(value) => value && setAdjustmentPolicy(value as 'RAW' | 'ADJUSTED')}><SelectTrigger id="strategy-policy" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ADJUSTED">Adjusted</SelectItem><SelectItem value="RAW">Raw</SelectItem></SelectContent></Select>
            </label>
          </div>

          <div className="border-t border-border/80">
            <div className="px-4 py-3"><h3 className="text-sm font-semibold">Versioned parameters</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">Defaults are research hypotheses, not optimized thresholds. Overrides are validated and fingerprinted.</p></div>
            <div className="grid gap-px border-t border-border/80 bg-border/70 sm:grid-cols-2 xl:grid-cols-3">
              {catalog.loading ? Array.from({ length: 6 }, (_, index) => <div key={index} className="bg-surface p-4"><Skeleton className="h-16 w-full rounded-none" /></div>) : strategy?.parameters.map((parameter) => <label key={parameter.code} htmlFor={`strategy-param-${parameter.code}`} className="grid gap-1.5 bg-surface p-4 text-xs font-medium text-muted-foreground"><span className="text-foreground">{parameter.display_name}</span>
                {parameter.value_type === 'BOOLEAN' ? <Select value={String(parameterValues[parameter.code])} onValueChange={(value) => value && updateParameter(parameter, value)}><SelectTrigger id={`strategy-param-${parameter.code}`} className="h-9 w-full rounded-none bg-surface-inset text-foreground"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="true">True</SelectItem><SelectItem value="false">False</SelectItem></SelectContent></Select> : <Input id={`strategy-param-${parameter.code}`} inputMode="decimal" value={String(parameterValues[parameter.code] ?? '')} min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} onChange={(event) => updateParameter(parameter, event.target.value)} className="h-9 rounded-none bg-surface-inset text-foreground" />}
                <span className="leading-4">{parameter.description}</span>
              </label>)}
            </div>
          </div>

          {evaluationError && <div className="border-t border-border/80 p-4"><Alert variant="destructive" className="rounded-none"><X /><AlertTitle>Evaluation could not run</AlertTitle><AlertDescription>{evaluationError}</AlertDescription></Alert></div>}
          <div className="flex flex-col justify-between gap-3 border-t border-border/80 bg-surface-inset/55 px-4 py-3 sm:flex-row sm:items-center"><p className="text-xs leading-5 text-muted-foreground">All eligible members are returned in symbol order · unavailable values never pass</p><Button onClick={runEvaluation} disabled={running || catalog.loading || !catalog.data?.universes.length} className="min-w-40 rounded-none"><Play className={running ? 'animate-pulse' : ''} />{running ? 'Evaluating…' : 'Evaluate strategy'}</Button></div>
        </Surface>

        <Surface inset>
          <SurfaceHeader title="Research contract" description={strategy?.description ?? 'Select a strategy to inspect its immutable evaluation contract.'} />
          <dl className="divide-y divide-border/80 text-sm">
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Registry key</dt><dd className="numeric mt-1.5 font-medium">{strategy ? `${strategy.strategy_code} · v${strategy.strategy_version}` : '—'}</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Feature contract</dt><dd className="numeric mt-1.5 break-words text-xs font-medium">{strategy?.required_feature_set ?? '—'}</dd><dd className="mt-1 text-xs text-primary">Version {strategy?.required_feature_set_version ?? '—'}</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Timing</dt><dd className="numeric mt-1.5 font-medium">{strategy?.evaluation_timing_policy ?? '—'}</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Minimum warm-up</dt><dd className="numeric mt-1.5 font-medium">{strategy ? `${strategy.minimum_warmup_observations} observations` : '—'}</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Result semantics</dt><dd className="mt-1.5 font-medium">Matched / not matched</dd></div>
          </dl>
          <div className="border-t border-primary/20 bg-primary/[0.045] px-4 py-3 text-xs leading-5 text-primary"><ShieldCheck className="mr-2 inline size-3.5" />Historical membership and available_at ≤ as_of are enforced.</div>
        </Surface>
      </div>

      {running ? <Surface className="mt-4"><SurfaceHeader title="Evaluating strategy" description="Resolving historical membership and computing strategy-support features in bounded batches." /><div className="space-y-2 p-4">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-10 w-full rounded-none" />)}</div></Surface> : evaluation && <>
        <div className="mt-4 grid border border-border/90 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Universe members" value={<span className="numeric">{formatInteger.format(evaluation.universe_member_count)}</span>} supporting={evaluation.universe.symbol} icon={Users} accent />
          <MetricCard label="Matched setups" value={<span className="numeric">{formatInteger.format(evaluation.matched_count)}</span>} supporting={`${evaluation.strategy.rules.length} required conditions`} icon={Check} />
          <MetricCard label="Unavailable" value={<span className="numeric">{formatInteger.format(evaluation.unavailable_security_count)}</span>} supporting="Exact-date or warm-up gaps" icon={Database} />
          <MetricCard label="Service latency" value={<span className="numeric">{evaluation.timings.total_service_ms.toFixed(1)} ms</span>} supporting="Backend computation" icon={Clock3} />
        </div>

        {!evaluation.matched_count && evaluation.universe_member_count > 0 && <Alert className="mt-4 rounded-none"><Beaker /><AlertTitle>No matched setups</AlertTitle><AlertDescription>Every historical universe member was still returned below. Review its condition breakdown for deterministic failures.</AlertDescription></Alert>}
        {evaluation.warnings.some((warning) => warning.includes('INSUFFICIENT_FEATURE_HISTORY')) && <Alert className="mt-4 rounded-none"><Database /><AlertTitle>Insufficient feature history</AlertTitle><AlertDescription>At least one member lacks a required point-in-time value. Missing features are never treated as zero and always produce not matched.</AlertDescription></Alert>}

        <Surface className="mt-4">
          <SurfaceHeader title="Evaluation results" description={`${evaluation.strategy.display_name} v${evaluation.strategy.strategy_version} · ${evaluation.universe.name} · ${formatDate(evaluation.observation_date)}`} action={<span className="numeric text-xs text-muted-foreground">{evaluation.matched_count} / {evaluation.universe_member_count}</span>} />
          {!evaluation.universe_member_count ? <NoResults message="This universe has no members on the selected historical date." /> : <div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[980px] text-[0.8125rem]"><TableHeader><TableRow className="hover:bg-transparent"><TableHead className="h-9 pl-4">Security</TableHead><TableHead className="h-9">Setup</TableHead><TableHead className="h-9">Rules</TableHead><TableHead className="h-9">Required feature values</TableHead><TableHead className="h-9">Version / policy</TableHead><TableHead className="h-9 pr-4">Condition detail</TableHead></TableRow></TableHeader><TableBody>{evaluation.results.map((result) => <TableRow key={result.security_id} className="interactive-row align-top hover:bg-primary/[0.035]"><TableCell className="py-3 pl-4"><Link href={`/securities/${result.symbol}`} className="focus-terminal numeric font-semibold text-primary hover:underline">{result.symbol}</Link><p className="mt-1 max-w-52 truncate text-xs text-muted-foreground">{result.company_name}</p></TableCell><TableCell className="py-3"><span className={`inline-flex items-center gap-1.5 border px-2 py-1 text-[0.6875rem] font-semibold uppercase tracking-[0.08em] ${result.matched ? 'border-success/30 bg-success/[0.07] text-success' : 'border-border bg-surface-inset text-muted-foreground'}`}>{result.matched ? <Check className="size-3" /> : <X className="size-3" />}{result.matched ? 'Matched' : 'Not matched'}</span></TableCell><TableCell className="numeric py-3 font-medium">{result.passed_condition_count} / {result.total_condition_count}<p className="mt-1 text-[0.625rem] text-muted-foreground">conditions passed</p></TableCell><TableCell className="py-3"><div className="grid gap-1">{Object.entries(result.required_feature_values).map(([code, value]) => <div key={code} className="flex min-w-64 justify-between gap-3 text-xs"><span className="numeric text-muted-foreground">{code}</span><span className="numeric font-medium">{displayValue(value, ruleMap.get(code)?.unit)}</span></div>)}</div></TableCell><TableCell className="py-3"><p className="numeric font-medium">v{result.strategy_version} · {result.adjustment_policy}</p><p className="numeric mt-1 text-[0.625rem] text-muted-foreground">{result.result_fingerprint.slice(0, 12)}…</p></TableCell><TableCell className="py-3 pr-4"><details className="group min-w-72"><summary className="focus-terminal cursor-pointer text-xs font-semibold text-primary hover:underline">View {result.total_condition_count} conditions</summary><p className="mt-2 text-xs leading-5 text-muted-foreground">{result.explanation}</p><div className="mt-2 divide-y divide-border/70 border border-border/80">{result.conditions.map((condition) => <div key={condition.feature_code} className="grid grid-cols-[1fr_auto] gap-3 bg-surface-inset/50 px-2.5 py-2 text-[0.6875rem]"><div><p className="numeric font-semibold">{condition.feature_code} <span className="text-muted-foreground">v{condition.feature_version}</span></p><p className="numeric mt-1 text-muted-foreground">actual {displayValue(condition.actual_value, condition.unit)} · required {condition.operator} {displayValue(condition.expected_value, condition.unit)}</p></div><span className={condition.passed ? 'text-success' : 'text-danger'}>{condition.passed ? 'PASS' : 'FAIL'}</span></div>)}</div>{result.warnings.length > 0 && <p className="mt-2 text-[0.6875rem] leading-5 text-warning">{result.warnings.join(' · ')}</p>}</details></TableCell></TableRow>)}</TableBody></Table></div>}
          <footer className="flex flex-col justify-between gap-2 border-t border-border/80 bg-surface-inset/45 px-4 py-3 text-[0.6875rem] text-muted-foreground sm:flex-row sm:items-center"><span>Evaluated {formatDateTime(evaluation.executed_at)} · {evaluation.result_order.replace('_', ' ').toLowerCase()}</span><span className="numeric">Evaluation {evaluation.evaluation_fingerprint.slice(0, 16)}…</span></footer>
        </Surface>
      </>}

      {!evaluation && !running && <Surface className="mt-4 border-dashed"><div className="grid min-h-44 place-items-center p-6 text-center"><div><span className="mx-auto grid size-10 place-items-center border border-primary/25 bg-primary/[0.065] text-primary"><FlaskConical className="size-4" /></span><h2 className="mt-3 text-sm font-semibold">Choose a versioned research setup</h2><p className="mt-1.5 max-w-xl text-sm leading-6 text-muted-foreground">Review the registry-defined parameters, set a historical clock, then evaluate. Every eligible member returns with explainable condition state and reproducibility fingerprints.</p></div></div></Surface>}
    </>}
  </TerminalShell>;
}
