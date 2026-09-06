'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpDown, Clock3, Database, Filter, ListFilter, Play, Plus, Search, ShieldCheck, Trash2, Users } from 'lucide-react';
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
import type { MarketScanRequest, MarketScanResponse, ScannerFeatureMetadata, ScannerFilter, ScannerOperator, ScannerResult } from '@/src/types/api';

interface FilterDraft {
  id: number;
  feature: string;
  operator: ScannerOperator;
  value: string;
  upperValue: string;
}

function istTimestamp(value: string): string {
  return `${value.length === 16 ? `${value}:00` : value}+05:30`;
}

function valueForRequest(filter: FilterDraft, feature: ScannerFeatureMetadata): ScannerFilter {
  const value = feature.value_type === 'BOOLEAN' ? filter.value === 'true' : filter.value;
  return {
    feature: filter.feature,
    operator: filter.operator,
    value,
    ...(filter.operator === 'between' ? { upper_value: filter.upperValue } : {}),
  };
}

function displayValue(value: string | boolean, feature?: ScannerFeatureMetadata): string {
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  if (feature?.unit === 'DECIMAL_FRACTION') return `${(numeric * 100).toFixed(2)}%`;
  if (feature?.unit === 'PRICE' || feature?.unit === 'CURRENCY') return formatPrice.format(numeric);
  if (feature?.unit === 'SHARES') return formatInteger.format(numeric);
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 4 }).format(numeric);
}

function compareResults(left: ScannerResult, right: ScannerResult, key: string): number {
  if (key === 'symbol') return left.symbol.localeCompare(right.symbol);
  const leftValue = left.matched_values[key];
  const rightValue = right.matched_values[key];
  if (typeof leftValue === 'boolean' && typeof rightValue === 'boolean') return Number(leftValue) - Number(rightValue);
  return Number(leftValue) - Number(rightValue);
}

export function MarketScannerWorkspace() {
  const metadata = useApi(useCallback((signal: AbortSignal) => api.scannerMetadata(signal), []));
  const nextFilterId = useRef(1);
  const initialized = useRef(false);
  const [universe, setUniverse] = useState('');
  const [observationDate, setObservationDate] = useState('');
  const [asOf, setAsOf] = useState('');
  const [adjustmentPolicy, setAdjustmentPolicy] = useState<'RAW' | 'ADJUSTED'>('ADJUSTED');
  const [filters, setFilters] = useState<FilterDraft[]>([]);
  const [scan, setScan] = useState<MarketScanResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: string; direction: 'asc' | 'desc' }>({ key: 'symbol', direction: 'asc' });

  const featureMap = useMemo(
    () => new Map(metadata.data?.features.map((feature) => [feature.code, feature]) ?? []),
    [metadata.data],
  );

  useEffect(() => {
    if (!metadata.data || initialized.current) return;
    initialized.current = true;
    const date = metadata.data.latest_observation_date ?? new Date().toISOString().slice(0, 10);
    const firstFeature = metadata.data.features[0];
    setUniverse(metadata.data.universes[0]?.symbol ?? '');
    setObservationDate(date);
    setAsOf(`${date}T15:30`);
    if (firstFeature) {
      setFilters([{
        id: nextFilterId.current++,
        feature: firstFeature.code,
        operator: firstFeature.supported_operators[0],
        value: firstFeature.value_type === 'BOOLEAN' ? 'true' : '0',
        upperValue: '',
      }]);
    }
  }, [metadata.data]);

  const updateFilter = (id: number, update: Partial<FilterDraft>) => {
    setFilters((current) => current.map((filter) => filter.id === id ? { ...filter, ...update } : filter));
  };

  const chooseFeature = (id: number, code: string) => {
    const feature = featureMap.get(code);
    if (!feature) return;
    updateFilter(id, {
      feature: code,
      operator: feature.supported_operators[0],
      value: feature.value_type === 'BOOLEAN' ? 'true' : '0',
      upperValue: '',
    });
  };

  const addFilter = () => {
    const feature = metadata.data?.features[0];
    if (!feature || filters.length >= 20) return;
    setFilters((current) => [...current, {
      id: nextFilterId.current++,
      feature: feature.code,
      operator: feature.supported_operators[0],
      value: feature.value_type === 'BOOLEAN' ? 'true' : '0',
      upperValue: '',
    }]);
  };

  const runScan = async () => {
    if (!metadata.data || !universe || !observationDate || !asOf || !filters.length) {
      setScanError('Complete the universe, date, as-of, and filter fields before running the scan.');
      return;
    }
    for (const filter of filters) {
      const feature = featureMap.get(filter.feature);
      if (!feature) {
        setScanError('One or more selected features are no longer available.');
        return;
      }
      if (feature.value_type === 'DECIMAL' && (!filter.value.trim() || !Number.isFinite(Number(filter.value)))) {
        setScanError(`${feature.display_name} requires a numeric value.`);
        return;
      }
      if (filter.operator === 'between' && (!filter.upperValue.trim() || !Number.isFinite(Number(filter.upperValue)))) {
        setScanError(`${feature.display_name} requires both minimum and maximum values.`);
        return;
      }
    }

    const payload: MarketScanRequest = {
      universe,
      observation_date: observationDate,
      as_of: istTimestamp(asOf),
      feature_set: metadata.data.feature_set.code,
      feature_set_version: metadata.data.feature_set.version,
      adjustment_policy: adjustmentPolicy,
      logic: 'AND',
      filters: filters.map((filter) => valueForRequest(filter, featureMap.get(filter.feature)!)),
    };
    setRunning(true);
    setScanError(null);
    try {
      setScan(await api.scanMarket(payload));
      setSort({ key: 'symbol', direction: 'asc' });
    } catch (error) {
      setScanError(error instanceof Error ? error.message : 'The scanner request could not be completed.');
    } finally {
      setRunning(false);
    }
  };

  const resultCodes = useMemo(
    () => Array.from(new Set(scan?.filters.map((filter) => filter.feature) ?? [])),
    [scan],
  );
  const sortedResults = useMemo(() => {
    if (!scan) return [];
    return [...scan.results].sort((left, right) => {
      const compared = compareResults(left, right, sort.key);
      return sort.direction === 'asc' ? compared : -compared;
    });
  }, [scan, sort]);

  const toggleSort = (key: string) => {
    setSort((current) => current.key === key
      ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
      : { key, direction: 'asc' });
  };

  return <TerminalShell title="Market scanner" eyebrow="Research">
    <PageHeader
      eyebrow="Point-in-time research"
      title="Market scanner"
      description="Evaluate a historical index membership against versioned technical conditions, using only data available at the selected as-of time."
      meta={<span className="inline-flex items-center gap-2 border border-success/25 bg-success/[0.055] px-2.5 py-1.5 text-xs font-medium text-success"><ShieldCheck className="size-3.5" />Read-only research</span>}
    />

    {metadata.error ? <RequestError message={metadata.error} retry={metadata.retry} /> : <>
      <div className="grid gap-4 xl:grid-cols-[1.5fr_.5fr]">
        <Surface>
          <SurfaceHeader eyebrow="Scan definition" title="Universe and evaluation clock" description="Membership is resolved on the observation date; feature rows must be available by the as-of timestamp." />
          <div className="grid gap-4 p-4 sm:grid-cols-2 xl:grid-cols-4">
            <label htmlFor="scanner-universe" className="grid gap-1.5 text-sm font-medium">Universe
              {metadata.loading ? <Skeleton className="h-9 w-full rounded-none" /> : <Select value={universe} onValueChange={(value) => value && setUniverse(value)}><SelectTrigger id="scanner-universe" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent align="start"><SelectGroup><SelectLabel>Historical indices</SelectLabel>{metadata.data?.universes.map((item) => <SelectItem key={item.id} value={item.symbol}>{item.name} · {item.symbol}</SelectItem>)}</SelectGroup></SelectContent></Select>}
            </label>
            <label htmlFor="scanner-observation-date" className="grid gap-1.5 text-sm font-medium">Observation date
              <Input id="scanner-observation-date" type="date" value={observationDate} onChange={(event) => { setObservationDate(event.target.value); setAsOf(`${event.target.value}T15:30`); }} className="h-9 rounded-none bg-surface-inset" />
            </label>
            <label htmlFor="scanner-as-of" className="grid gap-1.5 text-sm font-medium">As of · IST
              <Input id="scanner-as-of" type="datetime-local" value={asOf} onChange={(event) => setAsOf(event.target.value)} className="h-9 rounded-none bg-surface-inset" />
            </label>
            <label htmlFor="scanner-policy" className="grid gap-1.5 text-sm font-medium">Price policy
              <Select value={adjustmentPolicy} onValueChange={(value) => value && setAdjustmentPolicy(value as 'RAW' | 'ADJUSTED')}><SelectTrigger id="scanner-policy" className="h-9 w-full rounded-none bg-surface-inset"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ADJUSTED">Adjusted</SelectItem><SelectItem value="RAW">Raw</SelectItem></SelectContent></Select>
            </label>
          </div>

          <div className="border-t border-border/80">
            <div className="flex flex-col justify-between gap-3 px-4 py-3 sm:flex-row sm:items-center">
              <div><h3 className="text-sm font-semibold">Filter conditions</h3><p className="mt-1 text-xs text-muted-foreground">Every condition must match. Unavailable values never pass a filter.</p></div>
              <div className="flex items-center gap-2"><span className="border border-border bg-surface-inset px-2 py-1 text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground">AND logic</span><Button variant="outline" size="sm" onClick={addFilter} disabled={metadata.loading || filters.length >= 20}><Plus />Add filter</Button></div>
            </div>
            <div className="divide-y divide-border/75 border-t border-border/75">
              {metadata.loading ? <div className="space-y-2 p-4">{Array.from({ length: 2 }, (_, index) => <Skeleton key={index} className="h-10 w-full rounded-none" />)}</div> : filters.map((filter, index) => {
                const feature = featureMap.get(filter.feature);
                return <div key={filter.id} className="grid gap-2 px-4 py-3 md:grid-cols-[2rem_minmax(14rem,1.6fr)_minmax(7rem,.55fr)_minmax(7rem,.7fr)_auto] md:items-end">
                  <span className="numeric hidden h-9 items-center text-xs text-muted-foreground md:flex">{String(index + 1).padStart(2, '0')}</span>
                  <label htmlFor={`scanner-feature-${filter.id}`} className="grid gap-1.5 text-xs font-medium text-muted-foreground">Feature
                    <Select value={filter.feature} onValueChange={(value) => value && chooseFeature(filter.id, value)}><SelectTrigger id={`scanner-feature-${filter.id}`} className="h-9 w-full rounded-none bg-surface-inset text-foreground"><SelectValue /></SelectTrigger><SelectContent align="start">{Array.from(new Set(metadata.data?.features.map((item) => item.family) ?? [])).map((family) => <SelectGroup key={family}><SelectLabel className="capitalize">{family.replace('_', ' ')}</SelectLabel>{metadata.data?.features.filter((item) => item.family === family).map((item) => <SelectItem key={item.code} value={item.code}>{item.display_name} · {item.code}</SelectItem>)}</SelectGroup>)}</SelectContent></Select>
                  </label>
                  <label htmlFor={`scanner-operator-${filter.id}`} className="grid gap-1.5 text-xs font-medium text-muted-foreground">Operator
                    <Select value={filter.operator} onValueChange={(value) => value && updateFilter(filter.id, { operator: value as ScannerOperator, upperValue: value === 'between' ? filter.upperValue : '' })}><SelectTrigger id={`scanner-operator-${filter.id}`} className="h-9 w-full rounded-none bg-surface-inset text-foreground"><SelectValue /></SelectTrigger><SelectContent>{feature?.supported_operators.map((operator) => <SelectItem key={operator} value={operator}>{operator === 'between' ? 'Between' : operator}</SelectItem>)}</SelectContent></Select>
                  </label>
                  <div className={`grid gap-2 ${filter.operator === 'between' ? 'grid-cols-2' : ''}`}>
                    {feature?.value_type === 'BOOLEAN' ? <label htmlFor={`scanner-value-${filter.id}`} className="grid gap-1.5 text-xs font-medium text-muted-foreground">Value<Select value={filter.value} onValueChange={(value) => value && updateFilter(filter.id, { value })}><SelectTrigger id={`scanner-value-${filter.id}`} className="h-9 w-full rounded-none bg-surface-inset text-foreground"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="true">True</SelectItem><SelectItem value="false">False</SelectItem></SelectContent></Select></label> : <label htmlFor={`scanner-value-${filter.id}`} className="grid gap-1.5 text-xs font-medium text-muted-foreground">{filter.operator === 'between' ? 'Minimum' : 'Value'}<Input id={`scanner-value-${filter.id}`} inputMode="decimal" value={filter.value} onChange={(event) => updateFilter(filter.id, { value: event.target.value })} className="h-9 rounded-none bg-surface-inset text-foreground" /></label>}
                    {filter.operator === 'between' && <label htmlFor={`scanner-upper-${filter.id}`} className="grid gap-1.5 text-xs font-medium text-muted-foreground">Maximum<Input id={`scanner-upper-${filter.id}`} inputMode="decimal" value={filter.upperValue} onChange={(event) => updateFilter(filter.id, { upperValue: event.target.value })} className="h-9 rounded-none bg-surface-inset text-foreground" /></label>}
                  </div>
                  <Button variant="ghost" size="icon" aria-label={`Remove filter ${index + 1}`} onClick={() => setFilters((current) => current.filter((item) => item.id !== filter.id))} disabled={filters.length === 1} className="mb-0.5 text-muted-foreground hover:text-danger"><Trash2 /></Button>
                </div>;
              })}
            </div>
          </div>

          {scanError && <div className="border-t border-border/80 p-4"><Alert variant="destructive" className="rounded-none"><Filter /><AlertTitle>Scan could not run</AlertTitle><AlertDescription>{scanError}</AlertDescription></Alert></div>}
          <div className="flex flex-col justify-between gap-3 border-t border-border/80 bg-surface-inset/55 px-4 py-3 sm:flex-row sm:items-center">
            <p className="text-xs leading-5 text-muted-foreground">Up to 20 registry-backed conditions · no ranking, recommendations, or execution</p>
            <Button onClick={runScan} disabled={running || metadata.loading || !metadata.data?.universes.length} className="min-w-28 rounded-none"><Play className={running ? 'animate-pulse' : ''} />{running ? 'Running…' : 'Run scan'}</Button>
          </div>
        </Surface>

        <Surface inset>
          <SurfaceHeader title="Evaluation contract" description="Controls applied to every eligible universe member." />
          <dl className="divide-y divide-border/80 text-sm">
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Feature set</dt><dd className="numeric mt-1.5 font-medium">{metadata.data?.feature_set.code ?? '—'}</dd><dd className="mt-1 text-xs text-primary">Version {metadata.data?.feature_set.version ?? '—'}</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Universe rule</dt><dd className="mt-1.5 font-medium">Inclusive historical membership</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Availability gate</dt><dd className="numeric mt-1.5 font-medium">available_at ≤ as_of</dd></div>
            <div className="px-4 py-3"><dt className="text-xs text-muted-foreground">Result order</dt><dd className="mt-1.5 font-medium">Symbol ascending</dd></div>
          </dl>
          <div className="flex items-start gap-2.5 border-t border-primary/20 bg-primary/[0.045] px-4 py-3 text-xs leading-5 text-primary"><ShieldCheck className="mt-0.5 size-3.5 shrink-0" />Historical scans never substitute today&apos;s index members.</div>
        </Surface>
      </div>

      {running ? <Surface className="mt-4"><SurfaceHeader title="Evaluating universe" description="Resolving membership and computing point-in-time features in bounded batches." /><div className="space-y-2 p-4">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-10 w-full rounded-none" />)}</div></Surface> : scan && <>
        <div className="mt-4 grid border border-border/90 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Universe members" value={<span className="numeric">{formatInteger.format(scan.universe_member_count)}</span>} supporting={scan.universe.symbol} icon={Users} accent />
          <MetricCard label="Matched" value={<span className="numeric">{formatInteger.format(scan.matched_count)}</span>} supporting={`${scan.filters.length} AND condition${scan.filters.length === 1 ? '' : 's'}`} icon={ListFilter} />
          <MetricCard label="Unavailable" value={<span className="numeric">{formatInteger.format(scan.unavailable_security_count)}</span>} supporting="Exact-date or warm-up gaps" icon={Database} />
          <MetricCard label="Service latency" value={<span className="numeric">{scan.timings.total_service_ms.toFixed(1)} ms</span>} supporting="Backend computation" icon={Clock3} />
        </div>

        <Surface className="mt-4">
          <SurfaceHeader title="Scan results" description={`${scan.universe.name} · ${formatDate(scan.observation_date)} · ${scan.adjustment_policy.toLowerCase()} prices`} action={<span className="numeric text-xs text-muted-foreground">{scan.matched_count} / {scan.universe_member_count}</span>} />
          {!scan.universe_member_count ? <NoResults message="This universe has no members on the selected historical date." /> : !scan.results.length ? <NoResults message={scan.unavailable_security_count === scan.universe_member_count ? 'No universe member has an eligible, fully available feature row on this date.' : 'No eligible security satisfies every filter condition.'} /> : <Table className="text-[0.8125rem]"><TableHeader className="sticky top-0 z-10 bg-surface"><TableRow className="hover:bg-transparent"><TableHead className="h-9 pl-4"><button className="focus-terminal flex items-center gap-1.5" onClick={() => toggleSort('symbol')}>Security<ArrowUpDown className="size-3" /></button></TableHead>{resultCodes.map((code) => <TableHead key={code} className="h-9 text-right"><button className="focus-terminal ml-auto flex items-center gap-1.5" onClick={() => toggleSort(code)}>{code}<ArrowUpDown className="size-3" /></button></TableHead>)}<TableHead className="h-9">Available</TableHead><TableHead className="h-9 pr-4">Provenance</TableHead></TableRow></TableHeader><TableBody>{sortedResults.map((result) => <TableRow key={result.security_id} className="interactive-row hover:bg-primary/[0.035]"><TableCell className="py-3 pl-4"><Link href={`/securities/${result.symbol}`} className="focus-terminal numeric font-semibold text-primary hover:underline">{result.symbol}</Link><p className="mt-1 max-w-52 truncate text-xs text-muted-foreground">{result.company_name}</p></TableCell>{resultCodes.map((code) => <TableCell key={code} className="numeric py-3 text-right font-medium">{displayValue(result.matched_values[code], featureMap.get(code))}<p className="mt-1 text-[0.625rem] text-muted-foreground">v{result.feature_versions[code]}</p></TableCell>)}<TableCell className="py-3 text-xs text-muted-foreground">{formatDateTime(result.available_at)}</TableCell><TableCell className="py-3 pr-4"><p className="text-xs font-medium">{result.dataset.dataset_version}</p><p className="numeric mt-1 text-[0.625rem] text-muted-foreground">{result.input_fingerprint.slice(0, 12)}…</p></TableCell></TableRow>)}</TableBody></Table>}
          <footer className="flex flex-col justify-between gap-2 border-t border-border/80 bg-surface-inset/45 px-4 py-3 text-[0.6875rem] text-muted-foreground sm:flex-row sm:items-center"><span>Executed {formatDateTime(scan.executed_at)} · {scan.result_order.replace('_', ' ').toLowerCase()}</span><span className="numeric">Scan {scan.scan_fingerprint.slice(0, 16)}…</span></footer>
        </Surface>
      </>}

      {!scan && !running && <Surface className="mt-4 border-dashed"><div className="grid min-h-44 place-items-center p-6 text-center"><div><span className="mx-auto grid size-10 place-items-center border border-primary/25 bg-primary/[0.065] text-primary"><Search className="size-4" /></span><h2 className="mt-3 text-sm font-semibold">Define a historical screen</h2><p className="mt-1.5 max-w-lg text-sm leading-6 text-muted-foreground">Choose a universe and clock, add technical conditions, then run the scan. Results include exact feature versions and input fingerprints.</p></div></div></Surface>}
    </>}
  </TerminalShell>;
}
