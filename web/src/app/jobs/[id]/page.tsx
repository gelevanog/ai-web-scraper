"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { RecordTable } from "@/components/RecordTable";
import { Bar, Button, Card, Empty, ErrorBox, Page, Pill, Stat, StatusPill, StrategyPill, Table } from "@/components/ui";
import { API_URL, post, useApi } from "@/lib/api";
import { num, ruleText, shortUrl, when } from "@/lib/format";
import type { EventRow, ExtractorRow, Job, PageRow, SiftRecord } from "@/lib/types";

const TABS = ["overview", "records", "pages", "extractors"] as const;
type Tab = (typeof TABS)[number];

const LEVEL_TONE: Record<string, string> = { info: "text-ink-soft", warning: "text-amber", error: "text-rose" };
const KIND_ICON: Record<string, string> = {
  learned: "◆",
  healed: "✚",
  layout_changed: "⚠",
  heal_failed: "✖",
  injection: "⛨",
  robots_blocked: "⦸",
  browser: "◳",
  strategy: "➜",
  anomaly: "!",
  pii_removed: "⛨",
  deduplicated: "≡",
  job_finished: "✓",
};

function Events({ events }: { events: EventRow[] }) {
  const shown = events.filter((e) => e.kind !== "page_done").slice(-60);
  return (
    <div className="max-h-[520px] space-y-1 overflow-y-auto pr-1 text-[12.5px]">
      {shown.map((e) => (
        <div key={e.id} className="grid grid-cols-[66px_18px_1fr] gap-2">
          <span className="mono text-[11px] text-muted">{e.ts.slice(11, 19)}</span>
          <span className={LEVEL_TONE[e.level] ?? ""}>{KIND_ICON[e.kind] ?? "·"}</span>
          <span className={LEVEL_TONE[e.level] ?? ""}>{e.message}</span>
        </div>
      ))}
    </div>
  );
}

function Overview({ job, events, pages }: { job: Job; events: EventRow[]; pages: PageRow[] }) {
  const stats = job.stats;
  const progress = job.progress;
  const cap = Number(job.options.max_pages ?? 60);
  const pagesSeen = stats.pages ?? progress.pages ?? 0;
  const hosts = Object.entries(job.politeness.hosts ?? {});
  const healed = events.filter((e) => e.kind === "healed").length;
  const changed = events.filter((e) => e.kind === "layout_changed").length;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="pages" value={num(pagesSeen)} hint={`${num(stats.pages_unchanged ?? 0)} unchanged · ${num(stats.browser_pages ?? 0)} via browser`} />
        <Stat label="records" value={num(stats.records ?? progress.records)} hint={`${num(stats.duplicates_merged ?? 0)} duplicates merged`} tone="iris" />
        <Stat label="model calls" value={num(stats.llm?.calls ?? progress.llm_calls)} hint={stats.llm ? `${num((stats.llm.input_tokens + stats.llm.output_tokens) / 1000, 1)}k tokens` : "—"} tone="teal" />
        <Stat label="errors" value={num(stats.errors ?? progress.errors ?? 0)} hint={`${num(stats.robots_blocked ?? 0)} blocked by robots.txt`} tone={(stats.errors ?? 0) > 0 ? "rose" : undefined} />
        <Stat label="layout changes" value={`${changed} / ${healed}`} hint="detected / healed" tone={changed ? "amber" : undefined} />
        <Stat label="guards" value={`${num(stats.injection_findings ?? 0)} · ${num(stats.pii_removed ?? 0)}`} hint="injection findings · PII removed" />
      </div>
      {job.status === "running" || job.status === "queued" ? (
        <Card>
          <div className="mb-2 flex justify-between text-xs text-muted">
            <span>
              {pagesSeen} of at most {cap} pages
            </span>
            <span>{progress.records ?? 0} records so far</span>
          </div>
          <Bar value={pagesSeen / Math.max(cap, 1)} />
        </Card>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-[1fr_420px]">
        <Card title="Activity" aside={<span className="text-xs text-muted">{events.length} events</span>}>
          <Events events={events} />
        </Card>
        <div className="space-y-5">
          <Card title="Politeness (from the request log)">
            {hosts.length ? (
              <div className="space-y-3 text-xs">
                {hosts.map(([host, h]) => (
                  <div key={host}>
                    <div className="mono mb-1 text-[12px] text-ink">{host}</div>
                    <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-ink-soft">
                      <span>requests</span>
                      <span className="tabular-nums">
                        {h.requests} (+{h.subresources} browser subresources)
                      </span>
                      <span>robots.txt fetched</span>
                      <span className="tabular-nums">{h.robots_fetches}×</span>
                      <span>configured interval</span>
                      <span className="tabular-nums">{h.configured_interval_s} s</span>
                      <span>shortest real gap</span>
                      <span className="tabular-nums">{h.min_gap_s === null ? "—" : `${h.min_gap_s.toFixed(2)} s`}</span>
                      <span>average rate</span>
                      <span className="tabular-nums">{h.mean_requests_per_s ?? "—"} req/s</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty>No requests yet.</Empty>
            )}
          </Card>
          <Card title="Schema">
            <div className="mono space-y-0.5 text-[12px]">
              {(job.schema?.fields ?? []).map((f) => (
                <div key={f.name} className="flex justify-between gap-2">
                  <span className="text-ink">{f.name}</span>
                  <span className="text-muted">
                    {f.type}
                    {f.type === "money" ? ` ${f.currency ?? "USD"}` : ""}
                    {f.required ? " · req" : ""}
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-muted">{job.description}</p>
          </Card>
          <Card title="Pages by strategy">
            <div className="flex flex-wrap gap-2 text-xs">
              {Object.entries(
                pages.reduce<Record<string, number>>((acc, p) => {
                  acc[p.strategy || "skipped"] = (acc[p.strategy || "skipped"] ?? 0) + 1;
                  return acc;
                }, {}),
              ).map(([s, n]) => (
                <span key={s} className="flex items-center gap-1">
                  <StrategyPill strategy={s} /> {n}
                </span>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Records({ job }: { job: Job }) {
  const [q, setQ] = useState("");
  const [flag, setFlag] = useState("");
  const [minConf, setMinConf] = useState(0);
  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "200" });
    if (q) params.set("q", q);
    if (flag) params.set("flag", flag);
    if (minConf > 0) params.set("min_confidence", String(minConf));
    return `/api/jobs/${job.id}/records?${params.toString()}`;
  }, [job.id, q, flag, minConf]);
  const { data, error } = useApi<{ total: number; records: SiftRecord[] }>(query);
  return (
    <Card
      pad={false}
      title={`Records${data ? ` · ${data.total}` : ""}`}
      aside={
        <div className="flex gap-1.5">
          {["json", "jsonl", "csv", "parquet"].map((fmt) => (
            <a key={fmt} href={`${API_URL}/api/jobs/${job.id}/export?format=${fmt}`} className="rounded border border-line px-2 py-0.5 text-xs text-ink-soft hover:border-iris/40 hover:text-iris">
              {fmt}
            </a>
          ))}
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5 text-xs">
        <input className="w-56 rounded-md border border-line px-2 py-1" placeholder="search values…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="rounded-md border border-line bg-white px-2 py-1" value={flag} onChange={(e) => setFlag(e.target.value)}>
          <option value="">all records</option>
          <option value="anomaly">anomalies</option>
          <option value="not_on_page">values not found on the page</option>
          <option value="pii_removed">PII removed</option>
          <option value="dropped_extra_fields">extra fields dropped</option>
        </select>
        <label className="flex items-center gap-2 text-muted">
          min confidence
          <input type="range" min={0} max={1} step={0.05} value={minConf} onChange={(e) => setMinConf(Number(e.target.value))} />
          <span className="w-8 tabular-nums text-ink">{minConf.toFixed(2)}</span>
        </label>
      </div>
      {error ? <ErrorBox>{error}</ErrorBox> : null}
      <div className="px-2 pt-1">{data && job.schema ? <RecordTable records={data.records} fields={job.schema.fields} /> : null}</div>
    </Card>
  );
}

function Pages({ pages }: { pages: PageRow[] }) {
  return (
    <Card pad={false}>
      <Table dense head={["url", "status", "via", "role", "strategy", "extractor", "records", "similarity", "ms", "note"]}>
        {pages.map((p) => (
          <tr key={p.id}>
            <td className="mono max-w-[380px] truncate px-3 py-1.5 text-[11.5px]">{shortUrl(p.url)}</td>
            <td className={`px-3 py-1.5 tabular-nums ${p.status >= 400 || p.status === 0 ? "text-rose" : ""}`}>{p.status || "—"}</td>
            <td className="px-3 py-1.5">{p.via === "browser" ? <Pill tone="sky">browser</Pill> : p.via}</td>
            <td className="px-3 py-1.5 text-muted">{p.role}</td>
            <td className="px-3 py-1.5">{p.strategy ? <StrategyPill strategy={p.strategy} /> : null}</td>
            <td className="mono px-3 py-1.5 text-[11px] text-muted">{p.extractor ?? ""}</td>
            <td className="px-3 py-1.5 tabular-nums">{p.records}</td>
            <td className={`px-3 py-1.5 tabular-nums ${p.similarity !== null && p.similarity < 0.5 ? "text-amber" : "text-muted"}`}>{p.similarity === null ? "" : p.similarity.toFixed(2)}</td>
            <td className="px-3 py-1.5 tabular-nums text-muted">{p.ms ? p.ms.toFixed(0) : ""}</td>
            <td className="px-3 py-1.5 text-[11px] text-rose">{p.error ?? (p.unchanged ? <span className="text-muted">unchanged</span> : "")}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Extractors({ extractors }: { extractors: ExtractorRow[] }) {
  if (!extractors.length) return <Empty>No learned extractor: this job used direct LLM extraction.</Empty>;
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {extractors.map((x) => (
        <Card
          key={x.pk}
          title={
            <span className="normal-case">
              {x.host} · {x.kind} · v{x.version}
            </span>
          }
          aside={
            <div className="flex gap-1.5">
              <Pill tone={x.origin === "healed" ? "teal" : x.origin === "preloaded" ? "gray" : "iris"}>{x.origin}</Pill>
              <Pill tone={x.accepted ? "teal" : "amber"}>{x.accepted ? "validated" : "best effort"}</Pill>
              <Pill>{x.calls} call(s)</Pill>
            </div>
          }
        >
          <div className="mono grid gap-x-4 gap-y-1 text-[12px] md:grid-cols-[140px_1fr]">
            {x.state.program.item ? (
              <>
                <span className="text-muted">item</span>
                <span className="text-iris">{x.state.program.item}</span>
              </>
            ) : null}
            {Object.entries(x.state.program.fields).map(([name, rule]) => (
              <div key={name} className="contents">
                <span className="text-muted">{name}</span>
                <span className="break-all">{ruleText(rule)}</span>
              </div>
            ))}
            {x.state.program.next_page ? (
              <>
                <span className="text-muted">next page</span>
                <span>{ruleText(x.state.program.next_page)}</span>
              </>
            ) : null}
          </div>
          {x.parent ? <div className="mt-2 text-xs text-teal">re-learned from {x.parent}</div> : null}
          <div className="mt-3 text-xs text-muted">baseline: {x.state.baseline_items} items per page</div>
        </Card>
      ))}
    </div>
  );
}

function JobView() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const tab = (search.get("tab") as Tab) ?? "overview";
  const { data: job, error, reload } = useApi<Job>(`/api/jobs/${id}`);
  const { data: events, reload: reloadEvents } = useApi<EventRow[]>(`/api/jobs/${id}/events?limit=2000`);
  const { data: pages, reload: reloadPages } = useApi<PageRow[]>(`/api/jobs/${id}/pages`);
  const { data: extractors } = useApi<ExtractorRow[]>(tab === "extractors" ? `/api/jobs/${id}/extractors` : null);
  const running = job?.status === "running" || job?.status === "queued" || job?.status === "cancelling";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      reload();
      reloadEvents();
      reloadPages();
    }, 1500);
    return () => clearInterval(timer);
  }, [running, reload, reloadEvents, reloadPages]);

  if (error) return <Page title="Job">{<ErrorBox>{error}</ErrorBox>}</Page>;
  if (!job) return <Page title="Job">Loading…</Page>;
  return (
    <Page
      wide
      title={
        <span className="flex items-center gap-3">
          {job.name} <StatusPill status={job.status} />
        </span>
      }
      subtitle={
        <span className="mono text-[12px]">
          {job.id} · {job.model || "model pending"} · started {when(job.started_at)}
          {job.parent_id ? (
            <>
              {" "}
              · re-crawl of{" "}
              <Link className="text-iris" href={`/jobs/${job.parent_id}`}>
                {job.parent_id}
              </Link>
            </>
          ) : null}
        </span>
      }
      actions={
        <>
          {running ? (
            <Button kind="secondary" onClick={() => post(`/api/jobs/${job.id}/cancel`).then(reload)}>
              Cancel
            </Button>
          ) : (
            <Button kind="secondary" onClick={() => post<{ id: string }>(`/api/jobs/${job.id}/recrawl`).then((r) => router.push(`/jobs/${r.id}`))}>
              Re-crawl
            </Button>
          )}
        </>
      }
    >
      {job.error ? (
        <div className="mb-4">
          <ErrorBox>{job.error}</ErrorBox>
        </div>
      ) : null}
      <div className="mb-5 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <Link key={t} href={`/jobs/${job.id}?tab=${t}`} className={`-mb-px border-b-2 px-3 py-2 text-sm capitalize ${tab === t ? "border-iris font-medium text-iris" : "border-transparent text-muted hover:text-ink"}`}>
            {t}
            {t === "records" ? <span className="ml-1 text-xs text-muted">{job.record_count ?? ""}</span> : null}
          </Link>
        ))}
      </div>
      {tab === "overview" ? <Overview job={job} events={events ?? []} pages={pages ?? []} /> : null}
      {tab === "records" ? <Records job={job} /> : null}
      {tab === "pages" ? <Pages pages={pages ?? []} /> : null}
      {tab === "extractors" ? <Extractors extractors={extractors ?? []} /> : null}
    </Page>
  );
}

export default function JobPage() {
  return (
    <Suspense>
      <JobView />
    </Suspense>
  );
}
