"use client";

import Link from "next/link";

import { Card, Empty, ErrorBox, Page, Pill } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ruleText, when } from "@/lib/format";
import type { EventRow, Program, Rule } from "@/lib/types";

const STYLE: Record<string, { tone: "iris" | "amber" | "teal" | "rose" | "gray"; label: string; dot: string }> = {
  learned: { tone: "iris", label: "learned", dot: "bg-iris" },
  layout_changed: { tone: "amber", label: "layout changed", dot: "bg-amber" },
  healed: { tone: "teal", label: "healed", dot: "bg-teal" },
  heal_failed: { tone: "rose", label: "heal failed", dot: "bg-rose" },
  learn_failed: { tone: "rose", label: "learning failed", dot: "bg-rose" },
};

function Diff({ before, after }: { before: Program; after: Program }) {
  const names = Array.from(new Set([...Object.keys(before.fields ?? {}), ...Object.keys(after.fields ?? {})]));
  const rows: { name: string; a: string; b: string }[] = [{ name: "item", a: before.item ?? "—", b: after.item ?? "—" }];
  for (const name of names) rows.push({ name, a: ruleText((before.fields ?? {})[name] as Rule | undefined), b: ruleText((after.fields ?? {})[name] as Rule | undefined) });
  rows.push({ name: "next page", a: ruleText(before.next_page), b: ruleText(after.next_page) });
  return (
    <div className="mono mt-3 overflow-x-auto text-[11.5px]">
      <table className="w-full">
        <thead>
          <tr className="text-left text-[10px] tracking-wide text-muted uppercase">
            <th className="pb-1 pr-3 font-medium">field</th>
            <th className="pb-1 pr-3 font-medium">before (v1 layout)</th>
            <th className="pb-1 font-medium">after (re-learned)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-t border-line/60 align-top">
              <td className="py-1 pr-3 text-muted">{r.name}</td>
              <td className="py-1 pr-3 text-rose/80 line-through decoration-rose/30">{r.a}</td>
              <td className={`py-1 ${r.a === r.b ? "text-ink-soft" : "text-teal"}`}>{r.b}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function HealingPage() {
  const { data, error } = useApi<EventRow[]>("/api/timeline?limit=400");
  const events = (data ?? []).filter((e) => e.kind !== "strategy").reverse();
  const changed = events.filter((e) => e.kind === "layout_changed").length;
  const healed = events.filter((e) => e.kind === "healed").length;
  const failed = events.filter((e) => e.kind === "heal_failed").length;
  return (
    <Page
      title="Layout changes"
      subtitle="When a site is redesigned, learned selectors stop matching. Sift notices (records disappear, fields go empty), re-learns the rules from a few pages of the new layout, verifies them against records it extracted before, and only then trusts them. If verification fails, the site falls back to direct extraction."
      actions={
        <div className="flex gap-2 text-xs">
          <Pill tone="amber">{changed} detected</Pill>
          <Pill tone="teal">{healed} healed</Pill>
          <Pill tone="rose">{failed} failed</Pill>
        </div>
      }
    >
      {error ? <ErrorBox>{error}</ErrorBox> : null}
      {events.length === 0 ? <Empty>No layout changes yet. Re-crawl a job after its site changed (the demo re-crawls a shop after its redesign).</Empty> : null}
      <div className="relative ml-3 border-l-2 border-line pl-7">
        {events.map((e) => {
          const style = STYLE[e.kind] ?? { tone: "gray" as const, label: e.kind, dot: "bg-zinc-400" };
          const before = e.data.before as Program | undefined;
          const after = e.data.after as Program | undefined;
          const signals = (e.data.signals as string[] | undefined) ?? [];
          return (
            <div key={e.id} className="relative mb-4">
              <span className={`absolute top-4 -left-[37px] h-3.5 w-3.5 rounded-full border-2 border-white ${style.dot}`} />
              <Card>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Pill tone={style.tone}>{style.label}</Pill>
                  <span className="mono text-ink">{String(e.data.host ?? "")}</span>
                  <span className="text-muted">{when(e.ts)}</span>
                  {e.job_id ? (
                    <Link href={`/jobs/${e.job_id}`} className="mono ml-auto text-iris hover:underline">
                      {e.job_id}
                    </Link>
                  ) : null}
                </div>
                <div className="mt-1.5 text-sm text-ink">{e.message}</div>
                {signals.length ? (
                  <ul className="mt-2 list-disc pl-5 text-xs text-amber">
                    {signals.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                ) : null}
                {e.kind === "healed" && typeof e.data.reference_agreement === "number" ? (
                  <div className="mt-2 text-xs text-teal">agreement with earlier records: {(Number(e.data.reference_agreement) * 100).toFixed(0)}%</div>
                ) : null}
                {before && after ? <Diff before={before} after={after} /> : null}
              </Card>
            </div>
          );
        })}
      </div>
    </Page>
  );
}
