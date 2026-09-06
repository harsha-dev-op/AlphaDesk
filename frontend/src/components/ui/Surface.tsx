import { cn } from '@/lib/utils';

export function Surface({ children, className, inset = false }: { children: React.ReactNode; className?: string; inset?: boolean }) {
  return <section className={cn('surface-depth min-w-0 border border-border/90 bg-surface panel-shadow', inset && 'bg-surface-inset shadow-none', className)}>{children}</section>;
}

export function SurfaceHeader({ title, description, eyebrow, action, className }: { title: string; description?: string; eyebrow?: string; action?: React.ReactNode; className?: string }) {
  return <header className={cn('flex min-h-14 flex-col justify-between gap-3 border-b border-border/80 px-4 py-3 sm:flex-row sm:items-center', className)}><div className="min-w-0">{eyebrow && <p className="mb-1 text-[0.6875rem] font-semibold uppercase tracking-[0.13em] text-primary">{eyebrow}</p>}<h2 className="text-sm font-semibold tracking-tight text-foreground">{title}</h2>{description && <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>}</div>{action && <div className="shrink-0">{action}</div>}</header>;
}
