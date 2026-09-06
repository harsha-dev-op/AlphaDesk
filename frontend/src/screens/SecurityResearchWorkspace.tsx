'use client';

import { useCallback, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { Activity, ArrowDownRight, ArrowUpRight, BarChart3, CalendarRange, Database, Layers3, LockKeyhole, Scale } from 'lucide-react';
import { Button } from '@/components/ui/button';
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
import { formatDate, formatInteger, formatPrice } from '@/src/lib/format';
import { api } from '@/src/services/api';

export function SecurityResearchWorkspace() {
  const params = useParams<{ symbol: string }>();
  const symbol = decodeURIComponent(params?.symbol ?? '').toUpperCase();
  const [view, setView] = useState<'raw' | 'adjusted'>('raw');
  const security = useApi(useCallback((signal: AbortSignal) => api.security(symbol, signal), [symbol]));
  const prices = useApi(useCallback((signal: AbortSignal) => api.prices(symbol, view, signal), [symbol, view]));

  const summary = useMemo(() => {
    if (!prices.data?.items.length) return null;
    const items = prices.data.items;
    const first = Number(items[0].close);
    const latest = Number(items[items.length - 1].close);
    return {
      count: items.length,
      firstDate: items[0].trading_date,
      latestDate: items[items.length - 1].trading_date,
      latest,
      high: Math.max(...items.map((row) => Number(row.high))),
      low: Math.min(...items.map((row) => Number(row.low))),
      change: first ? ((latest / first) - 1) * 100 : null,
    };
  }, [prices.data]);

  const chooseView = (nextView: 'raw' | 'adjusted') => {
    if (nextView === view) return;
    setView(nextView);
    prices.retry();
  };

  const identity = security.data;

  return <TerminalShell title={symbol || 'Security research'} eyebrow="Research">
    <PageHeader eyebrow="Security workspace" title={identity?.company_name ?? symbol ?? 'Security'} description="Point-in-time identity, historical OHLCV, and versioned technical features. No live feed, signal, or recommendation is implied." meta={security.loading ? <Skeleton className="h-6 w-20 rounded-none" /> : <StatusBadge status={identity?.is_active ? 'ACTIVE' : 'INACTIVE'} />} />

    {security.error ? <div className="mb-4"><RequestError message={security.error} retry={security.retry} compact /></div> : <Surface className="mb-4">
      <div className="grid gap-0 xl:grid-cols-[1.15fr_1.85fr]">
        <div className="border-b border-border/80 p-4 xl:border-b-0 xl:border-r"><p className="text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Instrument identity</p>{security.loading ? <div className="mt-3 space-y-2"><Skeleton className="h-8 w-28 rounded-none" /><Skeleton className="h-4 w-52 rounded-none" /></div> : <><div className="mt-2 flex items-baseline gap-3"><span className="numeric text-2xl font-semibold tracking-[-0.035em] text-primary">{identity?.symbol}</span><span className="text-xs font-medium text-muted-foreground">{identity?.exchange} · {identity?.currency}</span></div><p className="mt-2 text-sm font-medium">{identity?.trading_symbol}</p></>}</div>
        <dl className="grid sm:grid-cols-2 lg:grid-cols-4">{[
          ['Sector', identity?.sector ?? 'Unclassified'], ['Industry', identity?.industry ?? 'Unclassified'], ['Security type', identity?.security_type], ['ISIN', identity?.isin ?? 'Not assigned'], ['Listing date', formatDate(identity?.listing_date)], ['Delisting date', identity?.delisting_date ? formatDate(identity.delisting_date) : 'Not delisted'], ['Exchange', identity?.exchange], ['Currency', identity?.currency],
        ].map(([label, value]) => <div key={label} className="min-w-0 border-b border-r border-border/70 px-3.5 py-3 last:border-r-0"><dt className="text-[0.625rem] font-semibold uppercase tracking-[0.11em] text-muted-foreground">{label}</dt><dd className="mt-1.5 truncate text-xs font-medium text-foreground" title={value}>{security.loading ? <Skeleton className="h-4 w-24 rounded-none" /> : value}</dd></div>)}</dl>
      </div>
    </Surface>}

    <Tabs defaultValue="overview" className="gap-4">
      <div className="terminal-scrollbar overflow-x-auto border-b border-border"><TabsList variant="line" className="h-9 min-w-max gap-4 p-0">
        <TabsTrigger value="overview" className="h-9 rounded-none px-1 text-xs">Overview</TabsTrigger>
        <TabsTrigger value="technicals" className="h-9 rounded-none px-1 text-xs"><Activity className="size-3" />Technicals</TabsTrigger>
        {['Fundamentals', 'Events', 'Signals', 'Backtests'].map((label) => <TabsTrigger key={label} value={label.toLowerCase()} disabled className="h-9 rounded-none px-1 text-xs"><LockKeyhole className="size-3" />{label}</TabsTrigger>)}
      </TabsList></div>
      <TabsContent value="overview" className="space-y-4">
        <div className="grid border border-border/90 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Latest close" value={prices.loading ? <Skeleton className="h-7 w-24 rounded-none" /> : summary ? <span className="numeric">{formatPrice.format(summary.latest)}</span> : 'Unavailable'} supporting={summary ? `Session ${formatDate(summary.latestDate)}` : 'No persisted rows'} icon={BarChart3} accent />
          <MetricCard label="Period change" value={prices.loading ? <Skeleton className="h-7 w-24 rounded-none" /> : summary?.change != null ? <span className={`numeric inline-flex items-center gap-1 ${summary.change >= 0 ? 'text-bullish' : 'text-bearish'}`}>{summary.change >= 0 ? <ArrowUpRight className="size-4" /> : <ArrowDownRight className="size-4" />}{Math.abs(summary.change).toFixed(2)}%</span> : 'Unavailable'} supporting="First close to latest close" icon={Scale} />
          <MetricCard label="Period range" value={prices.loading ? <Skeleton className="h-7 w-32 rounded-none" /> : summary ? <span className="numeric text-base">{formatPrice.format(summary.low)}–{formatPrice.format(summary.high)}</span> : 'Unavailable'} supporting="Observed low to high" icon={CalendarRange} />
          <MetricCard label="Observations" value={prices.loading ? <Skeleton className="h-7 w-16 rounded-none" /> : summary ? <span className="numeric">{formatInteger.format(summary.count)}</span> : '0'} supporting={summary ? `${formatDate(summary.firstDate)}–${formatDate(summary.latestDate)}` : 'No date coverage'} icon={Layers3} />
        </div>

        <Surface>
          <SurfaceHeader title="Daily price ledger" description="Corporate-action adjustments are derived at read time; persisted source rows remain unchanged." action={<div className="inline-flex border border-border bg-background p-0.5" aria-label="Price view"><Button size="sm" variant={view === 'raw' ? 'default' : 'ghost'} className="rounded-none" onClick={() => chooseView('raw')}>Raw</Button><Button size="sm" variant={view === 'adjusted' ? 'default' : 'ghost'} className="rounded-none" onClick={() => chooseView('adjusted')}>Adjusted</Button></div>} />
          {prices.error ? <RequestError message={prices.error} retry={prices.retry} /> : prices.loading ? <div className="space-y-2 p-4">{Array.from({ length: 10 }, (_, index) => <Skeleton key={index} className="h-9 w-full rounded-none" />)}</div> : !prices.data?.items.length ? <NoResults message="No OHLCV rows are available for this security. Ingest historical prices to populate this ledger." /> : <div className="terminal-scrollbar max-h-[34rem] overflow-auto"><Table className="min-w-[900px] text-[0.8125rem]">
            <TableHeader className="sticky top-0 z-10 bg-surface"><TableRow className="hover:bg-transparent"><TableHead className="h-9 pl-4 text-xs">Session</TableHead><TableHead className="h-9 text-right text-xs">Open</TableHead><TableHead className="h-9 text-right text-xs">High</TableHead><TableHead className="h-9 text-right text-xs">Low</TableHead><TableHead className="h-9 text-right text-xs">Close</TableHead><TableHead className="h-9 text-right text-xs">Volume</TableHead><TableHead className="h-9 text-right text-xs">Traded value</TableHead><TableHead className="h-9 text-right text-xs">Factor</TableHead><TableHead className="h-9 pr-4 text-right text-xs">Source</TableHead></TableRow></TableHeader>
            <TableBody>{prices.data.items.slice().reverse().map((row) => <TableRow key={row.trading_date} className="interactive-row h-10"><TableCell className="numeric py-1.5 pl-4 text-xs">{row.trading_date}</TableCell><TableCell className="numeric py-1.5 text-right">{formatPrice.format(Number(row.open))}</TableCell><TableCell className="numeric py-1.5 text-right text-bullish">{formatPrice.format(Number(row.high))}</TableCell><TableCell className="numeric py-1.5 text-right text-bearish">{formatPrice.format(Number(row.low))}</TableCell><TableCell className="numeric py-1.5 text-right font-semibold">{formatPrice.format(Number(row.close))}</TableCell><TableCell className="numeric py-1.5 text-right">{formatInteger.format(row.volume)}</TableCell><TableCell className="numeric py-1.5 text-right text-muted-foreground">{row.traded_value ? formatPrice.format(Number(row.traded_value)) : '—'}</TableCell><TableCell className="numeric py-1.5 text-right text-xs text-muted-foreground">{row.adjustment_factor}</TableCell><TableCell className="py-1.5 pr-4 text-right text-xs text-muted-foreground">{row.source}</TableCell></TableRow>)}</TableBody>
          </Table></div>}
          <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border/80 bg-surface-inset/60 px-4 py-2.5 text-[0.6875rem] text-muted-foreground"><span className="inline-flex items-center gap-1.5"><Database className="size-3" />Persisted historical data</span><span>View: <strong className="font-semibold text-foreground">{view}</strong></span><span>No live quote implied</span></footer>
        </Surface>
      </TabsContent>
      <TabsContent value="technicals"><TechnicalsWorkspace symbol={symbol} /></TabsContent>
    </Tabs>
  </TerminalShell>;
}
