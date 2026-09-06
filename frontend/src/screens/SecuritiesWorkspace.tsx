'use client';

import Link from 'next/link';
import { useCallback, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Search, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { TerminalShell } from '@/src/components/TerminalShell';
import { NoResults, RequestError } from '@/src/components/RequestState';
import { StatusBadge } from '@/src/components/StatusBadge';
import { PageHeader } from '@/src/components/ui/PageHeader';
import { Surface, SurfaceHeader } from '@/src/components/ui/Surface';
import { useApi } from '@/src/hooks/useApi';
import { formatInteger } from '@/src/lib/format';
import { api } from '@/src/services/api';
import type { Security } from '@/src/types/api';

type StatusFilter = 'all' | 'active' | 'inactive';
type SortField = 'symbol' | 'company_name' | 'sector' | 'exchange';
type SortDirection = 'asc' | 'desc';

const PAGE_SIZE = 25;

export function SecuritiesWorkspace() {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<StatusFilter>('all');
  const [sortField, setSortField] = useState<SortField>('symbol');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const result = useApi(useCallback((signal: AbortSignal) => api.securities(page, PAGE_SIZE, query.trim(), signal), [page, query]));

  const rows = useMemo(() => {
    const filtered = (result.data?.items ?? []).filter((security) => status === 'all' || (status === 'active' ? security.is_active : !security.is_active));
    return filtered.slice().sort((left, right) => {
      const a = String(left[sortField] ?? '').toLocaleLowerCase();
      const b = String(right[sortField] ?? '').toLocaleLowerCase();
      return a.localeCompare(b) * (sortDirection === 'asc' ? 1 : -1);
    });
  }, [result.data, sortDirection, sortField, status]);

  const totalPages = Math.max(1, Math.ceil((result.data?.total ?? 0) / PAGE_SIZE));
  const firstRow = result.data?.total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const lastRow = result.data ? Math.min(page * PAGE_SIZE, result.data.total) : 0;

  const changePage = (nextPage: number) => {
    setPage(nextPage);
    result.retry();
  };

  const changeSort = (field: SortField) => {
    if (sortField === field) setSortDirection((value) => value === 'asc' ? 'desc' : 'asc');
    else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const sortIcon = (field: SortField) => {
    if (field !== sortField) return <ArrowUpDown className="size-3 opacity-45" />;
    return sortDirection === 'asc' ? <ArrowUp className="size-3 text-primary" /> : <ArrowDown className="size-3 text-primary" />;
  };

  return <TerminalShell title="Security master" eyebrow="Market">
    <PageHeader eyebrow="Reference data" title="Security explorer" description="Search and inspect the canonical instrument universe without losing inactive or historically listed records." meta={<div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="size-1.5 bg-primary" />{result.data ? `${formatInteger.format(result.data.total)} matching instruments` : 'Awaiting security master'}</div>} />

    <Surface>
      <SurfaceHeader title="Instrument universe" description="Server-side symbol and company search · local sorting and status filter on the current page" />
      <div className="flex flex-col gap-3 border-b border-border/80 bg-surface-inset/55 p-3 lg:flex-row lg:items-center">
        <label htmlFor="security-search" className="relative block w-full lg:max-w-md"><span className="sr-only">Search securities</span><Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input id="security-search" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); result.retry(); }} placeholder="Search symbol or company name" className="focus-terminal h-8 rounded-none border-border bg-background pl-8 text-sm" /></label>
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2 lg:justify-end">
          <span className="flex items-center gap-1.5 text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground"><SlidersHorizontal className="size-3.5" />Filter</span>
          <select aria-label="Filter by active status" value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)} className="focus-terminal h-8 border border-border bg-background px-2 text-xs text-foreground outline-none">
            <option value="all">All statuses</option><option value="active">Active only</option><option value="inactive">Inactive only</option>
          </select>
          <span className="hidden text-xs text-muted-foreground sm:inline">{rows.length} visible on this page</span>
        </div>
      </div>

      {result.error ? <RequestError message={result.error} retry={result.retry} /> : result.loading ? <div className="space-y-2 p-4">{Array.from({ length: 10 }, (_, index) => <Skeleton key={index} className="h-10 w-full rounded-none" />)}</div> : !rows.length ? <NoResults message={result.data?.items.length ? 'No rows on this page match the selected status filter.' : 'No securities match this symbol or company search.'} /> : <div className="terminal-scrollbar max-w-full overflow-x-auto"><Table className="min-w-[780px] text-[0.8125rem]">
        <TableHeader className="sticky top-0 z-10 bg-surface"><TableRow className="hover:bg-transparent">
          {([['symbol', 'Symbol'], ['company_name', 'Company'], ['sector', 'Sector'], ['exchange', 'Exchange']] as const).map(([field, label]) => <TableHead key={field} className={field === 'symbol' ? 'h-9 pl-4' : 'h-9'}><button type="button" onClick={() => changeSort(field)} className="focus-terminal inline-flex items-center gap-1.5 rounded-sm text-xs font-semibold hover:text-foreground" aria-label={`Sort by ${label}`}>{label}{sortIcon(field)}</button></TableHead>)}
          <TableHead className="h-9 text-xs">Type</TableHead><TableHead className="h-9 text-right text-xs">Status</TableHead><TableHead className="h-9 pr-4 text-right text-xs"><span className="sr-only">Open</span></TableHead>
        </TableRow></TableHeader>
        <TableBody>{rows.map((security: Security) => <TableRow key={security.id} className="interactive-row h-11 hover:bg-primary/[0.035]">
          <TableCell className="py-2 pl-4"><Link href={`/securities/${security.symbol}`} className="focus-terminal numeric font-semibold text-primary hover:underline">{security.symbol}</Link></TableCell>
          <TableCell className="max-w-[25rem] py-2"><Link href={`/securities/${security.symbol}`} className="focus-terminal block truncate font-medium hover:text-primary">{security.company_name}</Link></TableCell>
          <TableCell className="max-w-[12rem] truncate py-2 text-muted-foreground">{security.sector ?? 'Unclassified'}</TableCell><TableCell className="numeric py-2">{security.exchange}</TableCell><TableCell className="py-2 text-xs text-muted-foreground">{security.security_type}</TableCell><TableCell className="py-2 text-right"><StatusBadge status={security.is_active ? 'ACTIVE' : 'INACTIVE'} compact /></TableCell><TableCell className="py-2 pr-4 text-right"><Button render={<Link href={`/securities/${security.symbol}`} aria-label={`Open ${security.symbol}`} />} nativeButton={false} variant="ghost" size="icon-xs"><ChevronRight /></Button></TableCell>
        </TableRow>)}</TableBody>
      </Table></div>}

      <footer className="flex min-h-12 flex-col gap-3 border-t border-border/80 px-4 py-3 text-xs text-muted-foreground sm:flex-row sm:items-center">
        <span>{result.data ? `Showing ${firstRow}–${lastRow} of ${formatInteger.format(result.data.total)}` : 'Pagination unavailable'}</span>
        <div className="flex items-center gap-2 sm:ml-auto"><span className="numeric mr-1">Page {page} / {totalPages}</span><Button variant="outline" size="icon-sm" disabled={page <= 1 || result.loading} onClick={() => changePage(page - 1)} aria-label="Previous page"><ChevronLeft /></Button><Button variant="outline" size="icon-sm" disabled={page >= totalPages || result.loading} onClick={() => changePage(page + 1)} aria-label="Next page"><ChevronRight /></Button></div>
      </footer>
    </Surface>
  </TerminalShell>;
}
