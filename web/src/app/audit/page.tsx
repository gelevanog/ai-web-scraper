"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Card, ErrorBox, Page, Pill, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { when } from "@/lib/format";
import type { EventRow } from "@/lib/types";

const KINDS = ["", "job_started", "strategy", "learned", "layout_changed", "healed", "heal_failed", "injection", "robots_blocked", "pii_removed", "anomaly", "browser", "fetch_error", "job_finished"];

export default function AuditPage() {
  const [level, setLevel] = useState("");
  const [kind, setKind] = useState("");
  const query = useMemo(() => {
    const p = new URLSearchParams({ limit: "400" });
    if (level) p.set("level", level);
    if (kind) p.set("kind", kind);
    return `/api/audit?${p.toString()}`;
  }, [level, kind]);
  const { data, error } = useApi<EventRow[]>(query);
  const rows = (data ?? []).filter((e) => e.kind !== "page_done").reverse();
  return (
    <Page title="Audit log" subtitle="Every decision Sift made, across all jobs: strategies chosen, extractors learned and healed, robots.txt refusals, quarantined prompt injections, personal data removed, anomalies flagged.">
      {error ? <ErrorBox>{error}</ErrorBox> : null}
      <Card pad={false}>
        <div className="flex flex-wrap gap-3 border-b border-line px-4 py-2.5 text-xs">
          <select className="rounded-md border border-line bg-white px-2 py-1" value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">all levels</option>
            <option value="info">info</option>
            <option value="warning">warning</option>
            <option value="error">error</option>
          </select>
          <select className="rounded-md border border-line bg-white px-2 py-1" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k || "all events"}
              </option>
            ))}
          </select>
          <span className="ml-auto self-center text-muted">{rows.length} events</span>
        </div>
        <Table dense head={["time", "level", "event", "message", "job"]}>
          {rows.map((e) => (
            <tr key={e.id} className="align-top">
              <td className="px-3 py-1.5 whitespace-nowrap text-muted">{when(e.ts)}</td>
              <td className="px-3 py-1.5">
                <Pill tone={e.level === "error" ? "rose" : e.level === "warning" ? "amber" : "gray"}>{e.level}</Pill>
              </td>
              <td className="mono px-3 py-1.5 text-[11.5px] text-iris">{e.kind}</td>
              <td className="max-w-[720px] px-3 py-1.5 text-ink">{e.message}</td>
              <td className="mono px-3 py-1.5 text-[11px]">
                {e.job_id ? (
                  <Link href={`/jobs/${e.job_id}`} className="text-muted hover:text-iris">
                    {e.job_id}
                  </Link>
                ) : null}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
    </Page>
  );
}
