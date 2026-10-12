"use client";

import Link from "next/link";
import { useEffect } from "react";

import { Card, Empty, ErrorBox, Page, StatusPill, StrategyPill, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, shortUrl, when } from "@/lib/format";
import type { Job } from "@/lib/types";

export default function JobsPage() {
  const { data: jobs, error, reload } = useApi<Job[]>("/api/jobs");
  const active = (jobs ?? []).some((j) => j.status === "running" || j.status === "queued");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(reload, 2000);
    return () => clearInterval(timer);
  }, [active, reload]);
  return (
    <Page title="Jobs" subtitle="Every crawl with its strategy, its yield and the model calls it cost. Re-crawls reuse the parent's extractors and skip unchanged pages.">
      {error ? <ErrorBox>{error}</ErrorBox> : null}
      <Card pad={false}>
        {jobs && jobs.length === 0 ? (
          <div className="p-4">
            <Empty>No jobs yet. Start one from New job.</Empty>
          </div>
        ) : (
          <Table head={["job", "status", "start URL", "strategy", "pages", "records", "model calls", "time", "created"]}>
            {(jobs ?? []).map((job) => (
              <tr key={job.id} className="hover:bg-zinc-50">
                <td className="px-3 py-2">
                  <Link href={`/jobs/${job.id}`} className="font-medium text-iris hover:underline">
                    {job.name}
                  </Link>
                  <div className="mono text-[11px] text-muted">
                    {job.id}
                    {job.parent_id ? <span className="ml-1 text-teal">re-crawl of {job.parent_id}</span> : null}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <StatusPill status={job.status} />
                </td>
                <td className="mono max-w-[260px] truncate px-3 py-2 text-[12px] text-ink-soft">{shortUrl(job.start_urls[0] ?? "")}</td>
                <td className="px-3 py-2">
                  <div className="flex gap-1">
                    {(job.stats.strategies ?? []).map((s) => (
                      <StrategyPill key={s} strategy={s} />
                    ))}
                  </div>
                </td>
                <td className="px-3 py-2 tabular-nums">{num(job.stats.pages ?? job.progress.pages)}</td>
                <td className="px-3 py-2 tabular-nums">{num(job.stats.records ?? job.progress.records)}</td>
                <td className="px-3 py-2 tabular-nums">{num(job.stats.llm?.calls ?? job.progress.llm_calls)}</td>
                <td className="px-3 py-2 tabular-nums text-muted">{job.stats.seconds ? `${job.stats.seconds.toFixed(1)} s` : "—"}</td>
                <td className="px-3 py-2 text-muted">{when(job.created_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </Page>
  );
}
