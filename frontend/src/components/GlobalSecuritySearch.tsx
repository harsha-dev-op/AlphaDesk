'use client';

import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, Search, ServerOff } from 'lucide-react';
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut } from '@/components/ui/command';
import { Kbd } from '@/components/ui/kbd';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/src/services/api';
import type { Security } from '@/src/types/api';

export function GlobalSecuritySearch() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Security[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  const search = (value: string) => {
    setQuery(value);
    setError(false);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    controllerRef.current?.abort();
    if (!value.trim()) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    timeoutRef.current = setTimeout(() => {
      const controller = new AbortController();
      controllerRef.current = controller;
      api.securities(1, 8, value.trim(), controller.signal)
        .then((response) => setResults(response.items))
        .catch(() => {
          if (!controller.signal.aborted) setError(true);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 180);
  };

  const navigate = (symbol: string) => {
    setOpen(false);
    setQuery('');
    setResults([]);
    window.location.assign(`/securities/${encodeURIComponent(symbol)}`);
  };

  return <>
    <button type="button" onClick={() => setOpen(true)} className="focus-terminal group flex h-8 w-full max-w-[32rem] items-center gap-2 border border-border/90 bg-surface-inset px-2.5 text-left text-sm text-muted-foreground transition-colors duration-150 hover:border-primary/40 hover:bg-surface">
      <Search className="size-3.5 shrink-0" /><span className="truncate">Search symbol or company</span><Kbd className="ml-auto hidden border border-border/80 bg-background/60 text-[0.625rem] sm:inline-flex">Ctrl K</Kbd>
    </button>
    <CommandDialog open={open} onOpenChange={setOpen} title="Search securities" description="Search the security master by ticker or company name" className="max-w-[38rem] border border-border bg-popover">
      <Command shouldFilter={false} className="rounded-none! bg-transparent">
        <CommandInput value={query} onValueChange={search} placeholder="Type a symbol or company name…" />
        <CommandList className="max-h-[22rem] p-1">
          {!query && <div className="px-3 py-8 text-center text-sm text-muted-foreground">Search the live AlphaDesk security master.</div>}
          {loading && <div className="space-y-2 p-2">{Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-12 w-full" />)}</div>}
          {error && <div className="flex items-center justify-center gap-2 px-3 py-8 text-sm text-danger"><ServerOff className="size-4" />Security search is unavailable.</div>}
          {!loading && !error && query && results.length === 0 && <CommandEmpty>No matching securities.</CommandEmpty>}
          {!loading && !error && results.length > 0 && <CommandGroup heading="Security master">{results.map((security) => <CommandItem key={security.id} value={security.symbol} onSelect={() => navigate(security.symbol)} className="min-h-12 px-3"><span className="numeric w-24 shrink-0 font-semibold text-primary">{security.symbol}</span><span className="min-w-0 flex-1"><span className="block truncate text-sm">{security.company_name}</span><span className="block truncate text-[0.625rem] text-muted-foreground sm:hidden">{security.exchange} · {security.sector ?? 'Unclassified'}</span></span><span className="hidden text-right text-xs text-muted-foreground sm:block"><span className="block">{security.exchange}</span><span className="block max-w-36 truncate text-[0.625rem]">{security.sector ?? 'Unclassified'}</span></span><CommandShortcut><ArrowUpRight className="size-3.5" /></CommandShortcut></CommandItem>)}</CommandGroup>}
        </CommandList>
      </Command>
    </CommandDialog>
  </>;
}
