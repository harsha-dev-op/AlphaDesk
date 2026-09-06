import { cn } from '@/lib/utils';

export function PageHeader({ eyebrow, title, description, meta, className }: { eyebrow: string; title: string; description: string; meta?: React.ReactNode; className?: string }) {
  return <header className={cn('mb-5 flex flex-col justify-between gap-4 border-b border-border/70 pb-5 lg:flex-row lg:items-end', className)}><div className="min-w-0"><p className="text-[0.6875rem] font-semibold uppercase tracking-[0.16em] text-primary">{eyebrow}</p><h1 className="mt-1 text-[1.65rem] font-semibold tracking-[-0.035em] text-foreground sm:text-[1.8rem]">{title}</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p></div>{meta && <div className="shrink-0">{meta}</div>}</header>;
}
