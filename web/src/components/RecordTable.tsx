"use client";

import { useState } from "react";

import { display, shortUrl } from "@/lib/format";
import type { FieldSpec, SiftRecord } from "@/lib/types";

function confidenceTone(value: number): string {
  if (value >= 0.9) return "bg-teal";
  if (value >= 0.6) return "bg-amber";
  return "bg-rose";
}

function Provenance({ record, field }: { record: SiftRecord; field: FieldSpec }) {
  const detail = record.provenance.detail?.fields?.[field.name];
  const prov = detail ?? record.provenance.fields?.[field.name];
  const url = detail ? record.provenance.detail?.url : record.provenance.url;
  const confidence = record.confidence[field.name] ?? 0;
  const notes = record.notes?.[field.name] ?? [];
  return (
    <div className="prov-pop rounded-lg border border-line bg-white p-3 text-left text-xs shadow-xl">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="font-semibold text-ink">{field.name}</span>
        <span className="flex items-center gap-1.5 text-muted">
          confidence
          <span className="inline-block h-1.5 w-12 overflow-hidden rounded-full bg-zinc-100">
            <span className={`block h-full ${confidenceTone(confidence)}`} style={{ width: `${confidence * 100}%` }} />
          </span>
          <span className="tabular-nums text-ink">{confidence.toFixed(2)}</span>
        </span>
      </div>
      <dl className="grid grid-cols-[64px_1fr] gap-x-2 gap-y-1">
        <dt className="text-muted">source</dt>
        <dd className="mono break-all text-sky">{url ? shortUrl(url) : "—"}</dd>
        {record.provenance.strategy === "learned" ? (
          <>
            <dt className="text-muted">rule</dt>
            <dd className="mono break-all text-iris">{prov?.rule ?? (detail ? "detail page" : "—")}</dd>
            <dt className="text-muted">node</dt>
            <dd className="mono break-all text-ink-soft">{prov?.node || "—"}</dd>
          </>
        ) : (
          <>
            <dt className="text-muted">by</dt>
            <dd className="mono break-all text-amber">{record.provenance.model ?? "model"} (direct)</dd>
          </>
        )}
        <dt className="text-muted">raw</dt>
        <dd className="mono break-all rounded bg-zinc-50 px-1 text-ink">{prov?.raw === undefined || prov?.raw === null ? "—" : typeof prov.raw === "string" ? prov.raw : JSON.stringify(prov.raw)}</dd>
        <dt className="text-muted">value</dt>
        <dd className="text-ink">{display(record.data[field.name])}</dd>
      </dl>
      {notes.length ? <div className="mt-2 border-t border-line pt-1.5 text-muted">{notes.join(" · ")}</div> : null}
    </div>
  );
}

export function RecordTable({ records, fields, pinned }: { records: SiftRecord[]; fields: FieldSpec[]; pinned?: { row: number; field: string } }) {
  const [hover, setHover] = useState<{ row: number; field: string } | null>(pinned ?? null);
  const active = hover ?? pinned ?? null;
  return (
    <div className="overflow-x-auto pb-48">
      <table className="w-full border-collapse text-left text-[12.5px]">
        <thead>
          <tr className="border-b border-line text-[11px] tracking-wide text-muted uppercase">
            <th className="px-2 py-2 font-medium">#</th>
            {fields.map((f) => (
              <th key={f.name} className="px-2 py-2 font-medium whitespace-nowrap">
                {f.name}
                <span className="ml-1 font-normal normal-case text-zinc-400">{f.type === "money" ? f.currency ?? "USD" : f.type}</span>
              </th>
            ))}
            <th className="px-2 py-2 font-medium">conf.</th>
            <th className="px-2 py-2 font-medium">flags</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line/70" onMouseLeave={() => setHover(null)}>
          {records.map((record, row) => (
            <tr key={record.id} className="align-top">
              <td className="px-2 py-1.5 text-muted tabular-nums">{row + 1}</td>
              {fields.map((f) => {
                const value = record.data[f.name];
                const isActive = active?.row === row && active.field === f.name;
                const low = (record.confidence[f.name] ?? 1) < 0.6 && value !== null && value !== undefined;
                return (
                  <td
                    key={f.name}
                    className={`cell-prov max-w-[240px] cursor-help px-2 py-1.5 ${isActive ? "bg-iris-soft" : ""} ${low ? "text-amber" : ""}`}
                    onMouseEnter={() => setHover({ row, field: f.name })}
                  >
                    <span className={`block truncate ${value === null || value === undefined ? "text-zinc-300" : ""}`}>{display(value)}</span>
                    {isActive ? <Provenance record={record} field={f} /> : null}
                  </td>
                );
              })}
              <td className="px-2 py-1.5 tabular-nums text-ink-soft">{record.record_confidence.toFixed(2)}</td>
              <td className="px-2 py-1.5">
                <div className="flex flex-wrap gap-1">
                  {record.flags.map((flag) => (
                    <span key={flag} className={`rounded px-1 py-0.5 text-[10px] ${flag.startsWith("anomaly") || flag.startsWith("not_on_page") ? "bg-rose-soft text-rose" : "bg-amber-soft text-amber"}`}>
                      {flag}
                    </span>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
