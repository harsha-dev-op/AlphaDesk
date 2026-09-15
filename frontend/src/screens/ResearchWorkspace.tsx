'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Beaker, CalendarRange, Check, Database, FileClock, Fingerprint, Layers3, Play, RefreshCw, Save, ShieldCheck, Users, X } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { MetricCard } from '@/src/components/ui/MetricCard';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { StatusBadge } from '@/src/components/StatusBadge';
import { TerminalShell } from '@/src/components/TerminalShell';
import { HistoricalResearchPanel } from '@/src/components/HistoricalResearchPanel';
import { useApi } from '@/src/hooks/useApi';
import { formatDate, formatDateTime, formatInteger } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { CompositionComponentRequest, CompositionEvaluationRequest, CompositionEvaluationResponse, CompositionStatus, ExperimentDefinition, ExperimentListItem, ExperimentRunListResponse, StrategyMetadata, StrategyParameterMetadata, StrategyScalar } from '@/src/types/api';

type WorkspaceView = 'composition' | 'experiments' | 'historical';

const strategyKey = (strategy: Pick<StrategyMetadata, 'strategy_code' | 'strategy_version'>) => `${strategy.strategy_code}:v${strategy.strategy_version}`;
const defaultsFor = (strategy: StrategyMetadata) => Object.fromEntries(strategy.parameters.map((parameter) => [parameter.code, parameter.default_value]));
const shortFingerprint = (value: string | null | undefined) => value ? `${value.slice(0, 16)}…${value.slice(-6)}` : '—';
const istTimestamp = (value: string) => new Date(`${value}:00+05:30`).toISOString();

function statusClass(status: CompositionStatus) {
  if (status === 'MATCHED') return 'border-success/30 bg-success/[0.07] text-success';
  if (status === 'INSUFFICIENT_FEATURE_HISTORY') return 'border-warning/30 bg-warning/[0.07] text-warning';
  return 'border-border bg-surface-inset text-muted-foreground';
}

function FingerprintRow({ label, value }: { label: string; value: string }) {
  return <div className="grid gap-1 border-b border-border/70 px-3 py-2.5 last:border-b-0 sm:grid-cols-[9rem_1fr] sm:items-center"><dt className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</dt><dd className="numeric break-all text-[0.6875rem] text-foreground" title={value}>{value}</dd></div>;
}

export function ResearchWorkspace() {
  const metadata = useApi(useCallback((signal: AbortSignal) => api.researchMetadata(signal), []));
  const initialized = useRef(false);
  const [view, setView] = useState<WorkspaceView>('composition');
  const [policyKey, setPolicyKey] = useState('');
  const [universe, setUniverse] = useState('');
  const [observationDate, setObservationDate] = useState('');
  const [asOf, setAsOf] = useState('');
  const [adjustmentPolicy, setAdjustmentPolicy] = useState<'RAW' | 'ADJUSTED'>('ADJUSTED');
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [parameterValues, setParameterValues] = useState<Record<string, Record<string, StrategyScalar>>>({});
  const [requiredCount, setRequiredCount] = useState(2);
  const [evaluation, setEvaluation] = useState<CompositionEvaluationResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);
  const [experimentName, setExperimentName] = useState('');
  const [experimentDescription, setExperimentDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [experiments, setExperiments] = useState<ExperimentListItem[]>([]);
  const [experimentTotal, setExperimentTotal] = useState(0);
  const [experimentsLoading, setExperimentsLoading] = useState(false);
  const [experimentsError, setExperimentsError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ExperimentDefinition | null>(null);
  const [runs, setRuns] = useState<ExperimentRunListResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [historicalExperimentId, setHistoricalExperimentId] = useState<string | null>(null);

  const strategiesByKey = useMemo(
    () => new Map(metadata.data?.strategies.map((strategy) => [strategyKey(strategy), strategy]) ?? []),
    [metadata.data],
  );
  const selectedDefinitions = useMemo(
    () => selectedStrategies.map((key) => strategiesByKey.get(key)).filter((item): item is StrategyMetadata => Boolean(item)),
    [selectedStrategies, strategiesByKey],
  );
  const selectedPolicy = metadata.data?.policies.find((policy) => `${policy.policy_code}:v${policy.policy_version}` === policyKey);

  useEffect(() => {
    if (!metadata.data || initialized.current) return;
    initialized.current = true;
    const date = metadata.data.latest_observation_date ?? new Date().toISOString().slice(0, 10);
    const initialStrategies = metadata.data.strategies.slice(0, 3);
    setPolicyKey(metadata.data.policies[0] ? `${metadata.data.policies[0].policy_code}:v${metadata.data.policies[0].policy_version}` : '');
    setUniverse(metadata.data.universes[0]?.symbol ?? '');
    setObservationDate(date);
    setAsOf(`${date}T15:30`);
    setSelectedStrategies(initialStrategies.map(strategyKey));
    setParameterValues(Object.fromEntries(initialStrategies.map((strategy) => [strategyKey(strategy), defaultsFor(strategy)])));
    setRequiredCount(Math.min(2, initialStrategies.length));
  }, [metadata.data]);

  const loadExperiments = useCallback(async () => {
    setExperimentsLoading(true);
    setExperimentsError(null);
    try {
      const response = await api.experiments(1, 50);
      setExperiments(response.items);
      setExperimentTotal(response.total);
    } catch (error) {
      setExperimentsError(error instanceof Error ? error.message : 'Saved experiments could not be loaded.');
    } finally {
      setExperimentsLoading(false);
    }
  }, []);

  const changeView = (nextView: WorkspaceView) => {
    setView(nextView);
    if (nextView === 'experiments' || nextView === 'historical') void loadExperiments();
  };

  const launchHistorical = (experimentId: string) => {
    setHistoricalExperimentId(experimentId);
    setView('historical');
    void loadExperiments();
  };

  const openExperiment = async (id: string) => {
    setDetailLoading(true);
    setReplayError(null);
    try {
      const [definition, history] = await Promise.all([api.experiment(id), api.experimentRuns(id, 1, 50)]);
      setDetail(definition);
      setRuns(history);
    } catch (error) {
      setReplayError(error instanceof Error ? error.message : 'Experiment detail could not be loaded.');
    } finally {
      setDetailLoading(false);
    }
  };

  const toggleStrategy = (strategy: StrategyMetadata) => {
    const key = strategyKey(strategy);
    if (selectedStrategies.includes(key)) {
      if (selectedStrategies.length <= 2) {
        setEvaluationError('A composition requires at least two strategy definitions.');
        return;
      }
      const next = selectedStrategies.filter((item) => item !== key);
      setSelectedStrategies(next);
      setRequiredCount((current) => Math.min(current, next.length));
      return;
    }
    if (selectedStrategies.length >= 10) {
      setEvaluationError('A composition supports at most ten strategy definitions.');
      return;
    }
    setSelectedStrategies((current) => [...current, key]);
    setParameterValues((current) => ({ ...current, [key]: defaultsFor(strategy) }));
  };

  const updateParameter = (strategy: StrategyMetadata, parameter: StrategyParameterMetadata, value: string) => {
    const key = strategyKey(strategy);
    setParameterValues((current) => ({
      ...current,
      [key]: {
        ...current[key],
        [parameter.code]: parameter.value_type === 'BOOLEAN' ? value === 'true' : value,
      },
    }));
  };

  const buildRequest = (): CompositionEvaluationRequest | null => {
    if (!selectedPolicy || !universe || !observationDate || !asOf || selectedDefinitions.length < 2) {
      setEvaluationError('Complete the policy, universe, research clock, and at least two strategies.');
      return null;
    }
    if (requiredCount < 1 || requiredCount > selectedDefinitions.length) {
      setEvaluationError('Required matches must be between 1 and the number of selected strategies.');
      return null;
    }
    for (const strategy of selectedDefinitions) {
      const values = parameterValues[strategyKey(strategy)] ?? {};
      for (const parameter of strategy.parameters) {
        if (parameter.value_type !== 'DECIMAL') continue;
        const raw = String(values[parameter.code] ?? '');
        const numeric = Number(raw);
        if (!raw.trim() || !Number.isFinite(numeric)) {
          setEvaluationError(`${strategy.display_name}: ${parameter.display_name} requires a numeric value.`);
          return null;
        }
        if (parameter.minimum !== null && numeric < Number(parameter.minimum)) {
          setEvaluationError(`${strategy.display_name}: ${parameter.display_name} must be at least ${parameter.minimum}.`);
          return null;
        }
        if (parameter.maximum !== null && numeric > Number(parameter.maximum)) {
          setEvaluationError(`${strategy.display_name}: ${parameter.display_name} must be at most ${parameter.maximum}.`);
          return null;
        }
      }
    }
    try {
      return {
        policy_code: selectedPolicy.policy_code,
        policy_version: selectedPolicy.policy_version,
        universe,
        observation_date: observationDate,
        as_of: istTimestamp(asOf),
        adjustment_policy: adjustmentPolicy,
        required_match_count: requiredCount,
        components: selectedDefinitions.map((strategy): CompositionComponentRequest => ({
          strategy_code: strategy.strategy_code,
          strategy_version: strategy.strategy_version,
          parameter_overrides: parameterValues[strategyKey(strategy)] ?? {},
        })),
      };
    } catch {
      setEvaluationError('The IST as-of timestamp is invalid.');
      return null;
    }
  };

  const runComposition = async () => {
    const request = buildRequest();
    if (!request) return;
    setRunning(true);
    setEvaluationError(null);
    setSavedId(null);
    try {
      setEvaluation(await api.evaluateComposition(request));
    } catch (error) {
      setEvaluationError(error instanceof Error ? error.message : 'The composition could not be evaluated.');
    } finally {
      setRunning(false);
    }
  };

  const saveExperiment = async () => {
    if (!evaluation || !experimentName.trim()) {
      setSaveError('Enter an experiment name before saving this exact evaluated configuration.');
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const response = await api.createExperiment({
        name: experimentName.trim(),
        description: experimentDescription.trim() || null,
        composition: evaluation.normalized_request,
      });
      setSavedId(response.experiment.id);
      setDetail(response.experiment);
      setRuns(response.experiment.latest_run ? { items: [response.experiment.latest_run], total: 1, page: 1, page_size: 50 } : null);
      await loadExperiments();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'The experiment could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  const replayExperiment = async () => {
    if (!detail) return;
    setReplaying(true);
    setReplayError(null);
    try {
      const response = await api.replayExperiment(detail.id);
      setDetail(response.experiment);
      setRuns(await api.experimentRuns(detail.id, 1, 50));
      await loadExperiments();
    } catch (error) {
      setReplayError(error instanceof Error ? error.message : 'The experiment replay could not be completed.');
    } finally {
      setReplaying(false);
    }
  };

  return <TerminalShell title="Research composition" eyebrow="Research">
    <PageHeader
      eyebrow="Multi-strategy provenance"
      title="Research composition"
      description="Compose immutable strategy definitions, preserve them as experiments, and replay the same N-of-M semantics through historical trade and portfolio research."
      meta={<span className="inline-flex items-center gap-2 border border-primary/25 bg-primary/[0.055] px-2.5 py-1.5 text-xs font-medium text-primary"><Layers3 className="size-3.5" />Phase 7–9 · research only</span>}
    />

    <Alert className="mb-4 rounded-none border-warning/25 bg-warning/[0.045] text-warning"><ShieldCheck /><AlertTitle>Composition research is not a recommendation</AlertTitle><AlertDescription>No ranking, prediction, live signal, broker order, or real-money execution is produced here. Historical results are simulations.</AlertDescription></Alert>

    <div className="mb-4 flex border border-border/90 bg-surface p-1" role="tablist" aria-label="Research workspace views">
      {(['composition', 'experiments', 'historical'] as const).map((item) => <button key={item} type="button" role="tab" aria-selected={view === item} onClick={() => changeView(item)} className={`focus-terminal min-h-9 min-w-0 flex-1 px-2 text-xs font-semibold uppercase tracking-[0.09em] sm:flex-none sm:px-4 ${view === item ? 'bg-primary/12 text-primary' : 'text-muted-foreground hover:bg-surface-inset hover:text-foreground'}`}>{item === 'composition' ? 'Composition' : item === 'historical' ? 'Historical analysis' : `Experiments${experimentTotal ? ` · ${experimentTotal}` : ''}`}</button>)}
    </div>

    {metadata.error ? <RequestError message={metadata.error} retry={metadata.retry} /> : view === 'composition' ? <>
      <div className="grid gap-4 xl:grid-cols-[1.5fr_.5fr]">
        <Surface>
          <SurfaceHeader eyebrow="Single research clock" title="Composition definition" description="Every selected strategy shares this universe, date, as-of boundary, and price policy." />
          <div className="grid gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3">
            <label htmlFor="research-policy" className="grid gap-1.5 text-sm font-medium">Policy / version
              {metadata.loading ? <Skeleton className="h-9 rounded-none" /> : <Select value={policyKey} onValueChange={(value) => value && setPolicyKey(value)}><SelectTrigger id="research-policy" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent>{metadata.data?.policies.map((policy) => <SelectItem key={`${policy.policy_code}:${policy.policy_version}`} value={`${policy.policy_code}:v${policy.policy_version}`}>{policy.display_name} · v{policy.policy_version}</SelectItem>)}</SelectContent></Select>}
            </label>
            <label htmlFor="research-universe" className="grid gap-1.5 text-sm font-medium">Universe
              <Select value={universe} onValueChange={(value) => value && setUniverse(value)}><SelectTrigger id="research-universe" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue placeholder="No universe available" /></SelectTrigger><SelectContent>{metadata.data?.universes.map((item) => <SelectItem key={item.id} value={item.symbol}>{item.name} · {item.symbol}</SelectItem>)}</SelectContent></Select>
            </label>
            <label htmlFor="research-required-count" className="grid gap-1.5 text-sm font-medium">Required matches · N of M
              <Input id="research-required-count" type="number" min={1} max={Math.max(1, selectedDefinitions.length)} value={requiredCount} onChange={(event) => setRequiredCount(Number(event.target.value))} className="h-9 rounded-none bg-surface-inset" />
            </label>
            <label htmlFor="research-observation-date" className="grid gap-1.5 text-sm font-medium">Observation date
              <Input id="research-observation-date" type="date" value={observationDate} onChange={(event) => { setObservationDate(event.target.value); setAsOf(`${event.target.value}T15:30`); }} className="h-9 rounded-none bg-surface-inset" />
            </label>
            <label htmlFor="research-as-of" className="grid gap-1.5 text-sm font-medium">As of · IST
              <Input id="research-as-of" type="datetime-local" value={asOf} onChange={(event) => setAsOf(event.target.value)} className="h-9 rounded-none bg-surface-inset" />
            </label>
            <label htmlFor="research-adjustment-policy" className="grid gap-1.5 text-sm font-medium">Price policy
              <Select value={adjustmentPolicy} onValueChange={(value) => value && setAdjustmentPolicy(value as 'RAW' | 'ADJUSTED')}><SelectTrigger id="research-adjustment-policy" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ADJUSTED">Adjusted</SelectItem><SelectItem value="RAW">Raw</SelectItem></SelectContent></Select>
            </label>
          </div>
          <div className="border-t border-border/80">
            <div className="flex items-end justify-between gap-3 px-4 py-3"><div><h3 className="text-sm font-semibold">Immutable strategy components</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">Select 2–10 registry definitions. Semantic identity is canonical and selection-order independent.</p></div><span className="numeric text-xs text-primary">M = {selectedDefinitions.length}</span></div>
            <div className="grid gap-px border-t border-border/80 bg-border/70 lg:grid-cols-3">
              {metadata.loading ? Array.from({ length: 3 }, (_, index) => <div key={index} className="bg-surface p-4"><Skeleton className="h-28 rounded-none" /></div>) : metadata.data?.strategies.map((strategy) => {
                const key = strategyKey(strategy);
                const selected = selectedStrategies.includes(key);
                return <div key={key} className={`bg-surface p-4 ${selected ? 'shadow-[inset_0_2px_0_var(--primary)]' : ''}`}><button type="button" aria-pressed={selected} onClick={() => toggleStrategy(strategy)} className={`focus-terminal flex w-full items-start justify-between gap-3 border p-3 text-left ${selected ? 'border-primary/35 bg-primary/[0.055]' : 'border-border bg-surface-inset/50 hover:border-primary/25'}`}><span><span className="block text-sm font-semibold">{strategy.display_name}</span><span className="numeric mt-1 block text-[0.625rem] text-muted-foreground">{strategy.strategy_code} · v{strategy.strategy_version}</span></span><span className={`grid size-5 shrink-0 place-items-center border ${selected ? 'border-primary bg-primary text-primary-foreground' : 'border-border'}`}>{selected && <Check className="size-3.5" />}</span></button>{selected && <div className="mt-3 grid gap-2">{strategy.parameters.map((parameter) => <label key={parameter.code} className="grid gap-1 text-[0.6875rem] font-medium text-muted-foreground"><span className="truncate text-foreground" title={parameter.description}>{parameter.display_name}</span>{parameter.value_type === 'BOOLEAN' ? <Select value={String(parameterValues[key]?.[parameter.code] ?? parameter.default_value)} onValueChange={(value) => value && updateParameter(strategy, parameter, value)}><SelectTrigger className="h-8 w-full rounded-none bg-surface-inset text-xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="true">True</SelectItem><SelectItem value="false">False</SelectItem></SelectContent></Select> : <Input inputMode="decimal" value={String(parameterValues[key]?.[parameter.code] ?? parameter.default_value)} min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} onChange={(event) => updateParameter(strategy, parameter, event.target.value)} className="h-8 rounded-none bg-surface-inset text-xs" />}</label>)}</div>}</div>;
              })}
            </div>
          </div>
          {evaluationError && <div className="border-t border-border/80 p-4"><Alert variant="destructive" className="rounded-none"><X /><AlertTitle>Composition could not run</AlertTitle><AlertDescription>{evaluationError}</AlertDescription></Alert></div>}
          <div className="flex flex-col justify-between gap-3 border-t border-border/80 bg-surface-inset/55 px-4 py-3 sm:flex-row sm:items-center"><p className="text-xs leading-5 text-muted-foreground">Feature dependencies are unioned · one bounded market-data pass · every member returned</p><Button onClick={runComposition} disabled={running || metadata.loading || !metadata.data?.universes.length} className="min-w-48 rounded-none"><Play className={running ? 'animate-pulse' : ''} />{running ? 'Evaluating…' : 'Evaluate composition'}</Button></div>
        </Surface>

        <Surface inset>
          <SurfaceHeader title="Composition contract" description={selectedPolicy?.description ?? 'Select a versioned policy.'} />
          <dl className="divide-y divide-border/80 text-sm">
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Registry key</dt><dd className="numeric mt-1.5 font-medium">{selectedPolicy ? `${selectedPolicy.policy_code} · v${selectedPolicy.policy_version}` : '—'}</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Threshold</dt><dd className="numeric mt-1.5 font-medium">{requiredCount} of {selectedDefinitions.length}</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Missing history</dt><dd className="mt-1.5 text-xs leading-5">Explicit when unavailable components could still meet N.</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Ordering</dt><dd className="mt-1.5 text-xs leading-5">Canonical strategy, version, and normalized parameters.</dd></div>
          </dl>
          <div className="border-t border-primary/20 bg-primary/[0.045] px-4 py-3 text-xs leading-5 text-primary"><ShieldCheck className="mr-2 inline size-3.5" />Historical membership and available_at ≤ as_of remain mandatory.</div>
        </Surface>
      </div>

      {running ? <Surface className="mt-4"><SurfaceHeader title="Evaluating composition" description="Resolving one historical universe and computing the unioned technical dependency set once." /><div className="space-y-2 p-4">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-10 rounded-none" />)}</div></Surface> : evaluation && <>
        <div className="mt-4 grid border border-border/90 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Universe members" value={<span className="numeric">{formatInteger.format(evaluation.universe_member_count)}</span>} supporting={evaluation.universe.symbol} icon={Users} accent />
          <MetricCard label="Matched" value={<span className="numeric">{formatInteger.format(evaluation.matched_count)}</span>} supporting={`${evaluation.normalized_request.required_match_count} of ${evaluation.components.length} required`} icon={Check} />
          <MetricCard label="Not matched" value={<span className="numeric">{formatInteger.format(evaluation.not_matched_count)}</span>} supporting="Deterministic component outcomes" icon={X} />
          <MetricCard label="Insufficient history" value={<span className="numeric">{formatInteger.format(evaluation.insufficient_history_count)}</span>} supporting={`${evaluation.timings.total_service_ms.toFixed(1)} ms service`} icon={Database} />
        </div>

        {evaluation.warnings.length > 0 && <Alert className="mt-4 rounded-none"><Database /><AlertTitle>Point-in-time data warnings</AlertTitle><AlertDescription>{evaluation.warnings.join(' · ')}</AlertDescription></Alert>}

        <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_.38fr]">
          <Surface>
            <SurfaceHeader title="Composition outcomes" description={`${evaluation.policy.display_name} v${evaluation.policy.policy_version} · ${formatDate(evaluation.observation_date)} · ${evaluation.adjustment_policy}`} action={<span className="numeric text-xs text-muted-foreground">{evaluation.matched_count} / {evaluation.universe_member_count}</span>} />
            <div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[1040px] text-[0.8125rem]"><TableHeader><TableRow className="hover:bg-transparent"><TableHead className="h-9 pl-4">Security</TableHead><TableHead className="h-9">Composition</TableHead><TableHead className="h-9">Consensus</TableHead><TableHead className="h-9">Component states</TableHead><TableHead className="h-9 pr-4">Audit details</TableHead></TableRow></TableHeader><TableBody>{evaluation.results.map((result) => <TableRow key={result.security_id} className="interactive-row align-top hover:bg-primary/[0.035]"><TableCell className="py-3 pl-4"><Link href={`/securities/${result.symbol}`} className="focus-terminal numeric font-semibold text-primary hover:underline">{result.symbol}</Link><p className="mt-1 max-w-48 truncate text-xs text-muted-foreground">{result.company_name}</p></TableCell><TableCell className="py-3"><span className={`inline-flex border px-2 py-1 text-[0.625rem] font-semibold uppercase tracking-[0.07em] ${statusClass(result.status)}`}>{result.status.replaceAll('_', ' ')}</span></TableCell><TableCell className="numeric py-3 font-medium">{result.matched_strategy_count} / {result.required_match_count}<p className="mt-1 text-[0.625rem] text-muted-foreground">{result.insufficient_strategy_count} insufficient</p></TableCell><TableCell className="py-3"><div className="grid min-w-56 gap-1.5">{result.component_results.map((component) => <div key={`${component.strategy_code}:${component.strategy_version}`} className="flex items-center justify-between gap-2 text-[0.6875rem]"><span className="numeric text-muted-foreground">{component.strategy_code}</span><span className={component.status === 'MATCHED' ? 'text-success' : component.status === 'INSUFFICIENT_FEATURE_HISTORY' ? 'text-warning' : 'text-muted-foreground'}>{component.status === 'INSUFFICIENT_FEATURE_HISTORY' ? 'INSUFFICIENT' : component.status.replace('_', ' ')}</span></div>)}</div></TableCell><TableCell className="py-3 pr-4"><details className="group min-w-80"><summary className="focus-terminal cursor-pointer text-xs font-semibold text-primary hover:underline">Open deterministic audit</summary><div className="mt-2 divide-y divide-border/70 border border-border/80">{result.component_results.map((component) => <div key={component.strategy_code} className="bg-surface-inset/45 p-2.5"><div className="flex items-center justify-between gap-3"><span className="numeric text-[0.6875rem] font-semibold">{component.strategy_code} · v{component.strategy_version}</span><span className="numeric text-[0.625rem] text-muted-foreground">{component.passed_condition_count}/{component.total_condition_count}</span></div><div className="mt-2 grid gap-1">{component.conditions.map((condition) => <div key={condition.feature_code} className="grid grid-cols-[1fr_auto] gap-3 text-[0.625rem]"><span className="numeric text-muted-foreground">{condition.feature_code} · {String(condition.actual_value ?? 'unavailable')} {condition.operator} {String(condition.expected_value)}</span><span className={condition.passed ? 'text-success' : 'text-danger'}>{condition.passed ? 'PASS' : 'FAIL'}</span></div>)}</div><p className="numeric mt-2 break-all text-[0.55rem] text-muted-foreground">{component.result_fingerprint}</p></div>)}</div></details></TableCell></TableRow>)}</TableBody></Table></div>
            <footer className="flex flex-col justify-between gap-2 border-t border-border/80 bg-surface-inset/45 px-4 py-3 text-[0.6875rem] text-muted-foreground sm:flex-row"><span>Evaluated {formatDateTime(evaluation.executed_at)} · {evaluation.result_order.replace('_', ' ').toLowerCase()}</span><span>{evaluation.union_feature_codes.length} union dependencies · one shared feature pass</span></footer>
          </Surface>

          <div className="grid content-start gap-4">
            <Surface inset><SurfaceHeader title="Reproducibility" action={<Fingerprint className="size-4 text-primary" />} /><dl className="border-t border-border/80"><FingerprintRow label="Config" value={evaluation.composition_config_fingerprint} /><FingerprintRow label="Dataset" value={evaluation.composition_dataset_fingerprint} /><FingerprintRow label="Run" value={evaluation.composition_run_fingerprint} /></dl></Surface>
            <Surface><SurfaceHeader title="Save exact experiment" description="Saving uses the normalized configuration attached to this displayed run, even if builder fields change afterward." /><div className="grid gap-3 p-4"><label htmlFor="experiment-name" className="grid gap-1.5 text-xs font-medium">Name<Input id="experiment-name" value={experimentName} maxLength={160} onChange={(event) => setExperimentName(event.target.value)} placeholder="e.g. Three-strategy March consensus" className="h-9 rounded-none bg-surface-inset" /></label><label htmlFor="experiment-description" className="grid gap-1.5 text-xs font-medium">Description · optional<Textarea id="experiment-description" value={experimentDescription} maxLength={2000} onChange={(event) => setExperimentDescription(event.target.value)} placeholder="Research intent or review notes" className="min-h-20 rounded-none bg-surface-inset" /></label>{saveError && <p className="text-xs leading-5 text-danger">{saveError}</p>}{savedId && <p className="numeric break-all border border-success/20 bg-success/[0.055] p-2 text-xs text-success">Saved experiment {savedId}</p>}<Button onClick={saveExperiment} disabled={saving} variant="outline" className="rounded-none"><Save />{saving ? 'Saving…' : 'Save experiment'}</Button></div></Surface>
          </div>
        </div>
      </>}

      {!evaluation && !running && <Surface className="mt-4 border-dashed"><div className="grid min-h-44 place-items-center p-6 text-center"><div><span className="mx-auto grid size-10 place-items-center border border-primary/25 bg-primary/[0.065] text-primary"><Beaker className="size-4" /></span><h2 className="mt-3 text-sm font-semibold">Compose immutable research definitions</h2><p className="mt-1.5 max-w-xl text-sm leading-6 text-muted-foreground">Choose at least two strategy versions and a consensus threshold. Results preserve missing-history ambiguity and never rank securities.</p></div></div></Surface>}
    </> : view === 'historical' ? metadata.data ? <HistoricalResearchPanel metadata={metadata.data} inlineComposition={evaluation?.normalized_request ?? null} experiments={experiments} initialExperimentId={historicalExperimentId} /> : <Surface><SurfaceHeader title="Loading historical research metadata" /><div className="space-y-2 p-4">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-10 rounded-none" />)}</div></Surface> : <>
      {experimentsError && <RequestError message={experimentsError} retry={() => void loadExperiments()} compact />}
      <div className="grid gap-4 xl:grid-cols-[.42fr_1fr]">
        <Surface>
          <SurfaceHeader eyebrow="Append-only provenance" title="Saved experiments" description="Definitions are immutable; every replay adds a new run." action={<Button variant="ghost" size="sm" onClick={() => void loadExperiments()} disabled={experimentsLoading} className="rounded-none"><RefreshCw className={experimentsLoading ? 'animate-spin' : ''} />Refresh</Button>} />
          {experimentsLoading && !experiments.length ? <div className="space-y-2 p-4">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-20 rounded-none" />)}</div> : !experiments.length ? <NoResults message="No saved experiments yet. Evaluate a composition and explicitly save it." compact /> : <div className="divide-y divide-border/80">{experiments.map((experiment) => <button key={experiment.id} type="button" onClick={() => void openExperiment(experiment.id)} className={`focus-terminal w-full p-4 text-left hover:bg-primary/[0.035] ${detail?.id === experiment.id ? 'bg-primary/[0.055] shadow-[inset_2px_0_0_var(--primary)]' : ''}`}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-sm font-semibold">{experiment.name}</p><p className="numeric mt-1 text-[0.625rem] text-muted-foreground">{experiment.required_match_count} of {experiment.strategy_count} · {experiment.universe} · {experiment.adjustment_policy}</p></div>{experiment.latest_replay_status && <StatusBadge status={experiment.latest_replay_status === 'REPRODUCED' ? 'SUCCESS' : experiment.latest_replay_status === 'INITIAL' ? 'ACTIVE' : 'WARNING'} label={experiment.latest_replay_status.replaceAll('_', ' ')} compact />}</div><div className="mt-2 flex justify-between gap-2 text-[0.625rem] text-muted-foreground"><span>{formatDate(experiment.observation_date)}</span><span>{formatDateTime(experiment.latest_run_at)}</span></div><p className="numeric mt-2 truncate text-[0.55rem] text-muted-foreground">{experiment.id}</p></button>)}</div>}
          <footer className="border-t border-border/80 bg-surface-inset/45 px-4 py-3 text-[0.6875rem] text-muted-foreground">{experimentTotal} persisted definition{experimentTotal === 1 ? '' : 's'} · first 50 shown</footer>
        </Surface>

        <div className="min-w-0">
          {detailLoading ? <Surface><SurfaceHeader title="Loading experiment" /><div className="space-y-2 p-4">{Array.from({ length: 7 }, (_, index) => <Skeleton key={index} className="h-10 rounded-none" />)}</div></Surface> : detail ? <div className="grid gap-4">
            <Surface>
              <SurfaceHeader eyebrow="Immutable definition" title={detail.name} description={detail.description ?? 'No description was saved.'} action={<div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => launchHistorical(detail.id)} className="rounded-none"><CalendarRange />Historical analysis</Button><Button onClick={replayExperiment} disabled={replaying} className="rounded-none"><RefreshCw className={replaying ? 'animate-spin' : ''} />{replaying ? 'Replaying…' : 'Replay experiment'}</Button></div>} />
              {replayError && <div className="p-4 pb-0"><Alert variant="destructive" className="rounded-none"><X /><AlertTitle>Replay could not complete</AlertTitle><AlertDescription>{replayError}</AlertDescription></Alert></div>}
              <div className="grid gap-px border-t border-border/80 bg-border/70 sm:grid-cols-2 xl:grid-cols-4"><div className="bg-surface p-3"><p className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Policy</p><p className="numeric mt-1 text-xs font-semibold">{detail.policy_code} · v{detail.policy_version}</p></div><div className="bg-surface p-3"><p className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Consensus</p><p className="numeric mt-1 text-xs font-semibold">{detail.normalized_request.required_match_count} of {detail.normalized_request.components.length}</p></div><div className="bg-surface p-3"><p className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Universe / date</p><p className="numeric mt-1 text-xs font-semibold">{detail.normalized_request.universe} · {formatDate(detail.normalized_request.observation_date)}</p></div><div className="bg-surface p-3"><p className="text-[0.625rem] uppercase tracking-[0.08em] text-muted-foreground">Price policy</p><p className="numeric mt-1 text-xs font-semibold">{detail.normalized_request.adjustment_policy}</p></div></div>
              <div className="border-t border-border/80 p-4"><h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">Normalized strategy components</h3><div className="mt-3 grid gap-2 lg:grid-cols-3">{detail.normalized_request.components.map((component) => <div key={`${component.strategy_code}:${component.strategy_version}`} className="border border-border bg-surface-inset/45 p-3"><p className="numeric text-xs font-semibold">{component.strategy_code} · v{component.strategy_version}</p><dl className="mt-2 grid gap-1">{Object.entries(component.parameter_overrides).map(([code, value]) => <div key={code} className="flex justify-between gap-3 text-[0.625rem]"><dt className="numeric text-muted-foreground">{code}</dt><dd className="numeric">{String(value)}</dd></div>)}</dl></div>)}</div></div>
              <dl className="border-t border-border/80"><FingerprintRow label="Definition" value={detail.config_fingerprint} /><FingerprintRow label="Experiment UUID" value={detail.id} /></dl>
            </Surface>

            <Surface>
              <SurfaceHeader title="Immutable run history" description="Initial execution and replays are ordered oldest first; no prior run is overwritten." action={<span className="numeric text-xs text-muted-foreground">{runs?.total ?? 0} runs</span>} />
              {!runs?.items.length ? <NoResults message="No run provenance is available." compact /> : <div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[900px] text-xs"><TableHeader><TableRow className="hover:bg-transparent"><TableHead className="h-9 pl-4">Executed</TableHead><TableHead className="h-9">Replay state</TableHead><TableHead className="h-9">Outcomes</TableHead><TableHead className="h-9">Dataset fingerprint</TableHead><TableHead className="h-9 pr-4">Run fingerprint / members</TableHead></TableRow></TableHeader><TableBody>{runs.items.map((run) => <TableRow key={run.id} className="align-top"><TableCell className="py-3 pl-4">{formatDateTime(run.executed_at)}<p className="numeric mt-1 text-[0.55rem] text-muted-foreground">{run.id}</p></TableCell><TableCell className="py-3"><StatusBadge status={run.replay_status === 'REPRODUCED' ? 'SUCCESS' : run.replay_status === 'INITIAL' ? 'ACTIVE' : 'WARNING'} label={run.replay_status.replaceAll('_', ' ')} compact /></TableCell><TableCell className="numeric py-3">{run.summary.matched} matched · {run.summary.not_matched} not<p className="mt-1 text-[0.625rem] text-warning">{run.summary.insufficient_history} insufficient</p></TableCell><TableCell className="numeric max-w-48 py-3 text-[0.625rem]" title={run.dataset_fingerprint}>{shortFingerprint(run.dataset_fingerprint)}</TableCell><TableCell className="py-3 pr-4"><p className="numeric text-[0.625rem]" title={run.run_fingerprint}>{shortFingerprint(run.run_fingerprint)}</p><details className="mt-2"><summary className="focus-terminal cursor-pointer text-[0.6875rem] font-semibold text-primary hover:underline">Inspect {run.member_outcomes.length} member outcomes</summary><div className="mt-2 grid gap-1 border border-border bg-surface-inset/50 p-2">{run.member_outcomes.map((member) => <div key={member.symbol} className="flex items-center justify-between gap-3 text-[0.625rem]"><span className="numeric font-semibold">{member.symbol}</span><span className={member.status === 'MATCHED' ? 'text-success' : member.status === 'INSUFFICIENT_FEATURE_HISTORY' ? 'text-warning' : 'text-muted-foreground'}>{member.status.replaceAll('_', ' ')} · {member.matched_strategy_count}/{member.required_match_count}</span></div>)}</div></details></TableCell></TableRow>)}</TableBody></Table></div>}
            </Surface>
          </div> : <Surface className="border-dashed"><div className="grid min-h-80 place-items-center p-6 text-center"><div><span className="mx-auto grid size-10 place-items-center border border-primary/25 bg-primary/[0.065] text-primary"><FileClock className="size-4" /></span><h2 className="mt-3 text-sm font-semibold">Select a saved experiment</h2><p className="mt-1.5 max-w-md text-sm leading-6 text-muted-foreground">Inspect immutable behavior configuration, append-only runs, fingerprints, outcomes, and replay drift status.</p></div></div></Surface>}
        </div>
      </div>
    </>}
  </TerminalShell>;
}
