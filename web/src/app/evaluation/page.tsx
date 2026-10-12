"use client";

import { Card, Empty, ErrorBox, Page, Pill, Stat, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, pct, usd } from "@/lib/format";

type Agg = { sites: number; truth_records: number; extracted_records: number; matched: number; precision: number; recall: number; field_accuracy: number; fields_correct: number; fields_total: number };
type CostRow = { calls_per_1k_pages: number; input_tokens_per_1k_pages: number; output_tokens_per_1k_pages: number; usd_per_1k_pages: Record<string, number>; field_accuracy?: number; pages?: number; calls?: number; call_seconds_p50?: number; extract_ms_p50?: number };
type Robustness = { sites: number; before_healing: Agg; after_healing: Agg; detected: number; healed: number; heal_failed: number; heal_calls: number; fallback_direct_calls: number; false_alarms_on_v1: number; incremental_unchanged_pages: number; incremental_pages: number; incremental_llm_calls: number };
type Summary = {
  available: boolean;
  synthetic?: { v1: Agg; by_vertical: Record<string, Agg>; by_field: Record<string, { correct: number; total: number; accuracy: number | null }>; robustness?: Robustness; model?: string; run_at?: string; v1_learned_only: Agg; v1_direct_only: Agg };
  strategies?: { learned: CostRow; direct: CostRow & { precision: number; recall: number }; same_pages?: { sites: number; learned_field_accuracy_all_pages: number; direct_field_accuracy_first_pages: number } };
  schema?: { sites: number; requested_fields: number; found: number; right_type: number; found_rate: number; right_type_rate: number; extra_fields: number; injected_fields_in_schema: number };
  traps?: { counterfactuals: Record<string, unknown>[]; traps: Record<string, unknown> };
  toscrape?: { books: Agg & { pages: number; llm: { calls: number } }; quotes: Agg & { pages: number; llm: { calls: number } }; direct_first_page: Agg; live_requests: number };
  models?: { name: string; model: string; v1: Agg; robustness?: Robustness; cost?: { llm_calls: number } }[];
  politeness?: { toscrape?: Record<string, { requests: number; min_gap_s: number | null; max_requests_per_s_10s: number; configured_interval_s: number; robots_fetches: number }> };
  ledger?: { requests: number; ok: number; retryable_errors: number; errors: number; requested_models: Record<string, number>; served_models: Record<string, number>; all_free: boolean; first: string; last: string; input_tokens: number; output_tokens: number };
};

function ratio(correct: number, total: number) {
  return total ? correct / total : null;
}

export default function EvaluationPage() {
  const { data, error } = useApi<Summary>("/api/evaluation");
  if (error) return <Page title="Evaluation">{<ErrorBox>{error}</ErrorBox>}</Page>;
  if (!data) return <Page title="Evaluation">Loading…</Page>;
  if (!data.available) return <Page title="Evaluation">{<Empty>No results yet: run `make eval-offline` or the real evaluation (see the README).</Empty>}</Page>;
  const s = data.synthetic;
  const r = s?.robustness;
  const st = data.strategies;
  const traps = data.traps?.traps as Record<string, Record<string, number>> | undefined;
  return (
    <Page
      wide
      title="Evaluation"
      subtitle={
        <>
          Measured on a synthetic web of 47 fictional sites (ground truth generated with it) and on books.toscrape.com and quotes.toscrape.com, with free models through OpenRouter only. Model: <span className="mono">{s?.model ?? "—"}</span>, run {s?.run_at?.slice(0, 10) ?? "—"}.
        </>
      }
    >
      {s ? (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="field accuracy (v1)" value={pct(s.v1.field_accuracy)} hint={`${num(s.v1.fields_correct)} of ${num(s.v1.fields_total)} fields`} tone="iris" />
          <Stat label="record recall" value={pct(s.v1.recall)} hint={`precision ${pct(s.v1.precision)}`} />
          <Stat label="redesign: before healing" value={r ? pct(r.before_healing.field_accuracy * r.before_healing.recall) : "—"} hint="accuracy × recall, v1 rules on v2" tone="rose" />
          <Stat label="after healing" value={r ? pct(r.after_healing.field_accuracy * r.after_healing.recall) : "—"} hint={r ? `${r.healed} of ${r.sites} sites healed` : ""} tone="teal" />
          <Stat label="calls / 1,000 pages" value={st ? `${num(st.learned.calls_per_1k_pages, 0)} vs ${num(st.direct.calls_per_1k_pages, 0)}` : "—"} hint="learned rules vs direct LLM" tone="amber" />
          <Stat label="real API calls" value={num(data.ledger?.requests)} hint={data.ledger?.all_free ? "every model id :free" : "check the ledger"} />
        </div>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-2">
        {s ? (
          <Card title="Accuracy by vertical (v1 layouts)" pad={false}>
            <Table dense head={["vertical", "sites", "records", "precision", "recall", "field accuracy"]}>
              {Object.entries(s.by_vertical).map(([v, a]) => (
                <tr key={v}>
                  <td className="px-3 py-1.5 font-medium">{v}</td>
                  <td className="px-3 py-1.5 tabular-nums">{a.sites}</td>
                  <td className="px-3 py-1.5 tabular-nums">{a.truth_records}</td>
                  <td className="px-3 py-1.5 tabular-nums">{pct(a.precision)}</td>
                  <td className="px-3 py-1.5 tabular-nums">{pct(a.recall)}</td>
                  <td className="px-3 py-1.5 tabular-nums font-medium">{pct(a.field_accuracy)}</td>
                </tr>
              ))}
            </Table>
          </Card>
        ) : null}
        {st ? (
          <Card title="Strategies: learned rules vs direct LLM extraction" pad={false}>
            <Table dense head={["", "learned rules", "direct LLM"]}>
              <tr>
                <td className="px-3 py-1.5 text-muted">field accuracy</td>
                <td className="px-3 py-1.5 tabular-nums">{pct(st.learned.field_accuracy)}</td>
                <td className="px-3 py-1.5 tabular-nums">{pct(st.direct.field_accuracy)}</td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">model calls per 1,000 pages</td>
                <td className="px-3 py-1.5 tabular-nums">{num(st.learned.calls_per_1k_pages, 1)}</td>
                <td className="px-3 py-1.5 tabular-nums">{num(st.direct.calls_per_1k_pages, 1)}</td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">tokens per 1,000 pages</td>
                <td className="px-3 py-1.5 tabular-nums">{num((st.learned.input_tokens_per_1k_pages + st.learned.output_tokens_per_1k_pages) / 1000, 0)}k</td>
                <td className="px-3 py-1.5 tabular-nums">{num((st.direct.input_tokens_per_1k_pages + st.direct.output_tokens_per_1k_pages) / 1000, 0)}k</td>
              </tr>
              {Object.keys(st.direct.usd_per_1k_pages ?? {}).map((model) => (
                <tr key={model}>
                  <td className="px-3 py-1.5 text-muted">est. cost / 1,000 pages at {model} prices</td>
                  <td className="px-3 py-1.5 tabular-nums">{usd(st.learned.usd_per_1k_pages[model])}</td>
                  <td className="px-3 py-1.5 tabular-nums">{usd(st.direct.usd_per_1k_pages[model])}</td>
                </tr>
              ))}
              <tr>
                <td className="px-3 py-1.5 text-muted">latency per page</td>
                <td className="px-3 py-1.5 tabular-nums">{st.learned.extract_ms_p50 ? `${num(st.learned.extract_ms_p50, 0)} ms (code)` : "—"}</td>
                <td className="px-3 py-1.5 tabular-nums">{st.direct.call_seconds_p50 ? `${num(st.direct.call_seconds_p50, 1)} s (model)` : "—"}</td>
              </tr>
            </Table>
          </Card>
        ) : null}
        {r ? (
          <Card title={`Robustness: the v2 redesign (${r.sites} sites with learned rules)`} pad={false}>
            <Table dense head={["", "records found", "field accuracy"]}>
              <tr>
                <td className="px-3 py-1.5 text-muted">v1 rules on v2, healing off</td>
                <td className="px-3 py-1.5 tabular-nums">{pct(r.before_healing.recall)}</td>
                <td className="px-3 py-1.5 tabular-nums">{pct(r.before_healing.field_accuracy)}</td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">healing on</td>
                <td className="px-3 py-1.5 tabular-nums">{pct(r.after_healing.recall)}</td>
                <td className="px-3 py-1.5 tabular-nums">{pct(r.after_healing.field_accuracy)}</td>
              </tr>
            </Table>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 px-4 py-3 text-xs text-ink-soft">
              <span>layout change detected</span>
              <span className="tabular-nums">
                {r.detected} of {r.sites}
              </span>
              <span>healed and verified</span>
              <span className="tabular-nums">{r.healed}</span>
              <span>heal failed → direct</span>
              <span className="tabular-nums">{r.heal_failed}</span>
              <span>model calls spent healing</span>
              <span className="tabular-nums">
                {r.heal_calls} (+{r.fallback_direct_calls} direct fallback)
              </span>
              <span>false alarms on unchanged v1</span>
              <span className="tabular-nums">{r.false_alarms_on_v1}</span>
              <span>re-crawl: unchanged pages skipped</span>
              <span className="tabular-nums">
                {r.incremental_unchanged_pages} of {r.incremental_pages}, {r.incremental_llm_calls} calls
              </span>
            </div>
          </Card>
        ) : null}
        {data.schema ? (
          <Card title="Schema from intent" pad={false}>
            <div className="grid grid-cols-3 gap-3 p-4">
              <Stat label="fields found" value={pct(data.schema.found_rate)} hint={`${data.schema.found} of ${data.schema.requested_fields}`} />
              <Stat label="with the right type" value={pct(data.schema.right_type_rate)} hint={`${data.schema.right_type} of ${data.schema.requested_fields}`} tone="iris" />
              <Stat label="injected fields kept" value={num(data.schema.injected_fields_in_schema)} hint={`${data.schema.extra_fields} extra fields proposed`} tone={data.schema.injected_fields_in_schema ? "rose" : "teal"} />
            </div>
          </Card>
        ) : null}
        {traps ? (
          <Card title="Traps" pad={false}>
            <Table dense head={["trap", "result"]}>
              <tr>
                <td className="px-3 py-1.5 text-muted">hidden items leaked</td>
                <td className="px-3 py-1.5">
                  {traps.hidden_items?.leaked ?? "—"} of {traps.hidden_items?.planted ?? "—"}
                </td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">duplicates left after dedupe</td>
                <td className="px-3 py-1.5">
                  {traps.duplicates?.left_after_dedupe ?? "—"} ({traps.duplicates?.merged ?? 0} merged)
                </td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">mixed currencies converted right</td>
                <td className="px-3 py-1.5">{traps.currency ? `${traps.currency.correct} of ${traps.currency.total} (${pct(ratio(traps.currency.correct, traps.currency.total))})` : "—"}</td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">relative dates resolved right</td>
                <td className="px-3 py-1.5">{traps.relative_dates ? `${traps.relative_dates.correct} of ${traps.relative_dates.total} (${pct(ratio(traps.relative_dates.correct, traps.relative_dates.total))})` : "—"}</td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">planted anomalies flagged</td>
                <td className="px-3 py-1.5">
                  {traps.anomalies?.found ?? "—"} of {traps.anomalies?.expected ?? "—"} ({traps.anomalies?.false ?? 0} false)
                </td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">personal data leaked</td>
                <td className="px-3 py-1.5">
                  {traps.pii?.leaked ?? "—"} of {traps.pii?.planted ?? "—"} planted
                </td>
              </tr>
              <tr>
                <td className="px-3 py-1.5 text-muted">robots.txt: disallowed items leaked</td>
                <td className="px-3 py-1.5">{traps.robots?.archived_items_leaked ?? "—"}</td>
              </tr>
            </Table>
          </Card>
        ) : null}
        {data.toscrape ? (
          <Card title="Public practice sites (toscrape.com)" pad={false}>
            <Table dense head={["site", "pages", "records", "precision", "recall", "field accuracy", "model calls"]}>
              {(["books", "quotes"] as const).map((k) => {
                const a = data.toscrape![k];
                return (
                  <tr key={k}>
                    <td className="px-3 py-1.5 font-medium">{k}.toscrape.com</td>
                    <td className="px-3 py-1.5 tabular-nums">{a.pages}</td>
                    <td className="px-3 py-1.5 tabular-nums">
                      {a.extracted_records ?? (a as unknown as { extracted: number }).extracted} / {a.truth_records ?? (a as unknown as { truth: number }).truth}
                    </td>
                    <td className="px-3 py-1.5 tabular-nums">{pct(a.precision)}</td>
                    <td className="px-3 py-1.5 tabular-nums">{pct(a.recall)}</td>
                    <td className="px-3 py-1.5 tabular-nums">{pct(a.field_accuracy)}</td>
                    <td className="px-3 py-1.5 tabular-nums">{a.llm.calls}</td>
                  </tr>
                );
              })}
            </Table>
            {data.politeness?.toscrape ? (
              <div className="space-y-1 px-4 py-3 text-xs text-ink-soft">
                {Object.entries(data.politeness.toscrape).map(([host, h]) => (
                  <div key={host}>
                    <span className="mono">{host}</span>: {h.requests} requests, shortest gap {h.min_gap_s?.toFixed(2) ?? "—"} s (limit {h.configured_interval_s} s), peak {h.max_requests_per_s_10s} req/s
                  </div>
                ))}
              </div>
            ) : null}
          </Card>
        ) : null}
        {data.models?.length ? (
          <Card title="Free model comparison (same subset)" pad={false}>
            <Table dense head={["model", "field accuracy", "recall", "healed", "model calls"]}>
              {data.models.map((m) => (
                <tr key={m.name}>
                  <td className="mono px-3 py-1.5 text-[11.5px]">{m.model}</td>
                  <td className="px-3 py-1.5 tabular-nums">{pct(m.v1.field_accuracy)}</td>
                  <td className="px-3 py-1.5 tabular-nums">{pct(m.v1.recall)}</td>
                  <td className="px-3 py-1.5 tabular-nums">{m.robustness ? `${m.robustness.healed} / ${m.robustness.sites}` : "—"}</td>
                  <td className="px-3 py-1.5 tabular-nums">{m.cost?.llm_calls ?? "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>
        ) : null}
        {data.ledger ? (
          <Card title="Cloud usage (call ledger)">
            <div className="space-y-1 text-xs text-ink-soft">
              <div>
                <b className="text-ink">{data.ledger.requests}</b> requests to OpenRouter ({data.ledger.ok} ok, {data.ledger.retryable_errors} retried, {data.ledger.errors} errors), {num((data.ledger.input_tokens + data.ledger.output_tokens) / 1e6, 2)}M tokens.
              </div>
              {Object.entries(data.ledger.requested_models).map(([m, n]) => (
                <div key={m} className="flex justify-between">
                  <span className="mono">{m}</span>
                  <span className="tabular-nums">{n}</span>
                </div>
              ))}
              <div className="pt-1">{data.ledger.all_free ? <Pill tone="teal">every requested and served model id ends in :free</Pill> : <Pill tone="rose">non-free model in the ledger</Pill>}</div>
            </div>
          </Card>
        ) : null}
      </div>
    </Page>
  );
}
