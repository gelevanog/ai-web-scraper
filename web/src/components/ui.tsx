import type { ReactNode } from "react";

export function Page({ title, subtitle, actions, children, wide = false }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`mx-auto px-7 py-7 ${wide ? "max-w-[1560px]" : "max-w-[1320px]"}`}>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight text-ink">{title}</h1>
          {subtitle ? <div className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

export function Card({ title, aside, children, className = "", pad = true }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`rounded-xl border border-line bg-card shadow-[0_1px_2px_rgba(20,26,46,0.04)] ${className}`}>
      {title ? (
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-[13px] font-semibold tracking-wide text-ink-soft uppercase">{title}</h2>
          {aside}
        </div>
      ) : null}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

const TONES = {
  iris: "bg-iris-soft text-iris",
  teal: "bg-teal-soft text-teal",
  amber: "bg-amber-soft text-amber",
  rose: "bg-rose-soft text-rose",
  sky: "bg-sky-soft text-sky",
  gray: "bg-zinc-100 text-zinc-600",
} as const;
export type Tone = keyof typeof TONES;

export function Pill({ tone = "gray", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATUS: Record<string, Tone> = { done: "teal", running: "iris", queued: "sky", failed: "rose", cancelled: "gray", cancelling: "amber" };

export function StatusPill({ status }: { status: string }) {
  return (
    <Pill tone={STATUS[status] ?? "gray"}>
      {status === "running" ? <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-iris" /> : null}
      {status}
    </Pill>
  );
}

export function StrategyPill({ strategy }: { strategy: string }) {
  if (strategy === "learned") return <Pill tone="iris" title="selectors learned once, no model call per page">learned rules</Pill>;
  if (strategy === "direct") return <Pill tone="amber" title="one model call per page">direct LLM</Pill>;
  if (strategy === "unchanged") return <Pill tone="gray" title="same content hash as last time">unchanged</Pill>;
  return <Pill>{strategy || "—"}</Pill>;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  const color = tone ? { iris: "text-iris", teal: "text-teal", amber: "text-amber", rose: "text-rose", sky: "text-sky", gray: "text-ink" }[tone] : "text-ink";
  return (
    <div className="rounded-xl border border-line bg-card px-4 py-3">
      <div className="text-[11px] font-medium tracking-wide text-muted uppercase">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{children}</div>;
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-rose/30 bg-rose-soft px-4 py-3 text-sm text-rose">{children}</div>;
}

export function Button({ children, onClick, kind = "primary", disabled, type = "button" }: { children: ReactNode; onClick?: () => void; kind?: "primary" | "secondary" | "ghost"; disabled?: boolean; type?: "button" | "submit" }) {
  const style = {
    primary: "bg-iris text-white hover:bg-indigo-700 disabled:bg-indigo-300",
    secondary: "border border-line bg-white text-ink hover:border-iris/40 hover:text-iris disabled:text-muted",
    ghost: "text-iris hover:bg-iris-soft",
  }[kind];
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition ${style}`}>
      {children}
    </button>
  );
}

export function Spinner() {
  return <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />;
}

export function Bar({ value, tone = "iris" }: { value: number; tone?: Tone }) {
  const color = { iris: "bg-iris", teal: "bg-teal", amber: "bg-amber", rose: "bg-rose", sky: "bg-sky", gray: "bg-zinc-400" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-100">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

export function Table({ head, children, dense = false }: { head: ReactNode[]; children: ReactNode; dense?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className={`w-full border-collapse text-left ${dense ? "text-[12.5px]" : "text-sm"}`}>
        <thead>
          <tr className="border-b border-line text-[11px] tracking-wide text-muted uppercase">
            {head.map((h, i) => (
              <th key={i} className="px-3 py-2 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line/70">{children}</tbody>
      </table>
    </div>
  );
}
