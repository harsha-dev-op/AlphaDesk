'use client';

import Link from 'next/link';
import { useCallback } from 'react';
import { usePathname } from 'next/navigation';
import { Activity, BarChart3, Bot, BriefcaseBusiness, ChartCandlestick, Database, FileSearch, FlaskConical, LayoutDashboard, LockKeyhole, Newspaper, Search, Settings, ShieldCheck } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail, SidebarSeparator, SidebarTrigger } from '@/components/ui/sidebar';
import { GlobalSecuritySearch } from '@/src/components/GlobalSecuritySearch';
import { StatusBadge } from '@/src/components/StatusBadge';
import { useApi } from '@/src/hooks/useApi';
import { api } from '@/src/services/api';

const groups = [
  { label: 'Workspace', items: [{ href: '/', label: 'Dashboard', icon: LayoutDashboard }] },
  { label: 'Market', items: [{ href: '', label: 'Markets', icon: ChartCandlestick, future: true }, { href: '/scanner', label: 'Scanner', icon: Search }, { href: '/securities', label: 'Securities', icon: BarChart3 }, { href: '', label: 'News & events', icon: Newspaper, future: true }] },
  { label: 'Research', items: [{ href: '/strategies', label: 'Strategies', icon: FlaskConical }, { href: '/backtests', label: 'Backtests', icon: FileSearch }, { href: '/portfolio', label: 'Portfolio', icon: BriefcaseBusiness }] },
  { label: 'Intelligence', items: [{ href: '', label: 'AI copilot', icon: Bot, future: true }] },
  { label: 'System', items: [{ href: '/data-health', label: 'Data health', icon: ShieldCheck }, { href: '', label: 'Settings', icon: Settings, future: true }] },
];

export function TerminalShell({ title, eyebrow, children }: { title: string; eyebrow: string; children: React.ReactNode }) {
  const pathname = usePathname() ?? '';
  const health = useApi(useCallback((signal: AbortSignal) => api.health(signal), []));
  return <TooltipProvider delay={250}><SidebarProvider style={{ '--sidebar-width': '14.75rem', '--sidebar-width-icon': '3.25rem' } as React.CSSProperties}>
    <Sidebar collapsible="icon" className="border-r border-sidebar-border/80">
      <SidebarHeader className="h-16 justify-center border-b border-sidebar-border/90 px-3">
        <Link href="/" className="focus-terminal flex items-center gap-2.5 overflow-hidden rounded-sm px-1 py-1">
          <span className="relative grid size-8 shrink-0 place-items-center border border-primary/30 bg-primary/[0.09] text-primary"><Activity className="size-[1.05rem]" /><span className="absolute -right-px -top-px size-1.5 bg-primary" /></span>
          <span className="min-w-0"><strong className="block truncate text-[0.95rem] font-semibold tracking-[-0.02em] text-white">AlphaDesk</strong><span className="block truncate text-[0.625rem] font-medium uppercase tracking-[0.14em] text-sidebar-foreground/45">Research terminal</span></span>
        </Link>
      </SidebarHeader>
      <SidebarContent className="terminal-scrollbar overflow-x-hidden py-2">
        {groups.map((group, groupIndex) => <div key={group.label}>{groupIndex > 0 && <SidebarSeparator className="my-1.5" />}<SidebarGroup className="px-2 py-1"><SidebarGroupLabel className="h-7 px-2 text-[0.625rem] font-semibold uppercase tracking-[0.14em] text-sidebar-foreground/38">{group.label}</SidebarGroupLabel><SidebarGroupContent><SidebarMenu className="gap-0.5">{group.items.map((item) => <SidebarMenuItem key={`${group.label}-${item.label}`}>
          {item.future ? <SidebarMenuButton disabled tooltip={`${item.label} · future phase`} className="text-sidebar-foreground/34"><item.icon /><span>{item.label}</span><LockKeyhole className="ml-auto size-3! opacity-55" /></SidebarMenuButton> : <SidebarMenuButton render={<Link href={item.href} />} isActive={item.href === '/' ? pathname === '/' : pathname.startsWith(item.href)} tooltip={item.label} className="relative data-active:bg-primary/[0.10] data-active:text-primary data-active:before:absolute data-active:before:inset-y-1.5 data-active:before:left-0 data-active:before:w-0.5 data-active:before:bg-primary"><item.icon /><span>{item.label}</span></SidebarMenuButton>}
        </SidebarMenuItem>)}</SidebarMenu></SidebarGroupContent></SidebarGroup></div>)}
      </SidebarContent>
      <SidebarFooter className="border-t border-sidebar-border/90 p-2"><SidebarMenu><SidebarMenuItem><SidebarMenuButton tooltip="Local Phase 6 research workspace · no broker connection" className="h-10 text-sidebar-foreground/60"><span className="grid size-7 shrink-0 place-items-center border border-sidebar-border bg-sidebar-accent/45"><Database className="size-3.5" /></span><span className="min-w-0"><span className="block truncate text-xs font-medium text-sidebar-foreground/80">Local workspace</span><span className="block truncate text-[0.625rem]">No broker connection</span></span><SidebarMenuBadge className="text-[0.55rem] text-primary">P6</SidebarMenuBadge></SidebarMenuButton></SidebarMenuItem></SidebarMenu></SidebarFooter>
      <SidebarRail />
    </Sidebar>
    <SidebarInset className="min-w-0 bg-background workspace-backdrop">
      <header className="terminal-topbar sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border/90 bg-background/94 px-3 backdrop-blur-xl sm:px-5">
        <SidebarTrigger className="shrink-0" /><div className="hidden min-w-32 border-l border-border/80 pl-3 xl:block"><p className="text-[0.625rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{eyebrow}</p><p className="truncate text-sm font-medium text-foreground">{title}</p></div>
        <div className="mx-auto flex w-full max-w-[32rem] justify-center"><GlobalSecuritySearch /></div>
        <div className="ml-auto flex shrink-0 items-center gap-2"><span className="hidden border border-border bg-surface-inset px-2 py-1 text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground sm:inline-flex">Local</span>{health.loading ? <span className="h-6 w-20 animate-pulse bg-muted" /> : <StatusBadge status={health.data?.status ?? 'unavailable'} label={health.data?.status === 'healthy' ? 'Systems' : undefined} compact />}</div>
      </header>
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-5 sm:px-6 lg:px-7">{children}</main>
    </SidebarInset>
  </SidebarProvider></TooltipProvider>;
}
