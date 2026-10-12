"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { RecordTable } from "@/components/RecordTable";
import { SchemaEditor } from "@/components/SchemaEditor";
import { Button, Card, ErrorBox, Page, Pill, Spinner, StrategyPill } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { ruleText } from "@/lib/format";
import type { Preview, Proposal, Schema, Site } from "@/lib/types";

const PRACTICE = [
  { id: "books", label: "books.toscrape.com", url: "https://books.toscrape.com/", intent: "Every book with its title, price in GBP, availability (in stock or not), star rating from 1 to 5, its category and its UPC from the book's own page. Follow the pagination." },
  { id: "quotes", label: "quotes.toscrape.com", url: "https://quotes.toscrape.com/", intent: "Every quote with its text, the author and the list of tags. Follow the pagination." },
];

function Builder() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: sites } = useApi<Site[]>("/api/sites");
  const [urls, setUrls] = useState("");
  const [description, setDescription] = useState("");
  const [schema, setSchema] = useState<Schema | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<"" | "propose" | "preview" | "run">("");
  const [error, setError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState("auto");
  const [maxPages, setMaxPages] = useState(60);
  const [heal, setHeal] = useState(true);

  const presets = useMemo(() => {
    const synthetic = (sites ?? []).filter((s) => ["shop-02", "shop-04", "jobs-01", "homes-01", "events-02", "menu-03", "saas-01", "registry-02", "spa-shop-06"].includes(s.id));
    return [...PRACTICE.map((p) => ({ id: p.id, label: p.label, url: p.url, intent: p.intent })), ...synthetic.map((s) => ({ id: s.id, label: `${s.name} (synthetic)`, url: s.start_url, intent: s.intent }))];
  }, [sites]);

  // ?preset=books fills the form once the presets are known (state adjusted during render, not in an effect).
  const [appliedPreset, setAppliedPreset] = useState<string | null>(null);
  const preset = params.get("preset");
  if (preset && preset !== appliedPreset && presets.length) {
    const chosen = presets.find((p) => p.id === preset);
    setAppliedPreset(preset);
    if (chosen) {
      setUrls(chosen.url);
      setDescription(chosen.intent);
    }
  }

  const firstUrl = urls.split(/\s+/).find(Boolean) ?? "";

  async function propose() {
    setBusy("propose");
    setError(null);
    setPreview(null);
    try {
      const result = await post<Proposal>("/api/schema/propose", { url: firstUrl, description });
      setProposal(result);
      if (result.schema) setSchema(result.schema);
      else setError(result.error ?? "no schema proposed");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  async function runPreview() {
    if (!schema) return;
    setBusy("preview");
    setError(null);
    try {
      setPreview(await post<Preview>("/api/preview", { url: firstUrl, description, schema, strategy }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  async function run() {
    setBusy("run");
    try {
      const created = await post<{ id: string }>("/api/jobs", { start_urls: urls.split(/\s+/).filter(Boolean), description, schema, strategy, max_pages: maxPages, heal });
      router.push(`/jobs/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy("");
    }
  }

  const program = preview?.extractors?.[0]?.program;
  const strategyEvent = preview?.events.find((e) => e.kind === "strategy");

  return (
    <Page
      wide
      title="Describe the data you want"
      subtitle="Sift proposes a typed schema from your request, learns selectors from a sample page, and shows the records with where every value came from before you run the whole crawl."
    >
      <div className="grid gap-5 xl:grid-cols-[400px_1fr]">
        <div className="space-y-5">
          <Card title="1 · Where and what">
            <label className="mb-1 block text-xs font-medium text-muted">Start URL(s), one per line</label>
            <textarea className="mono h-16 w-full rounded-lg border border-line px-3 py-2 text-[12.5px] focus:border-iris focus:outline-none" value={urls} onChange={(e) => setUrls(e.target.value)} placeholder="https://books.toscrape.com/" />
            <label className="mt-3 mb-1 block text-xs font-medium text-muted">What to extract, in plain English</label>
            <textarea className="h-28 w-full rounded-lg border border-line px-3 py-2 text-sm focus:border-iris focus:outline-none" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Every product with name, price in USD, rating, stock status and image URL; follow pagination." />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {presets.map((p) => (
                <button
                  key={p.id}
                  className="rounded-full border border-line bg-white px-2 py-0.5 text-[11px] text-ink-soft hover:border-iris/50 hover:text-iris"
                  onClick={() => {
                    setUrls(p.url);
                    setDescription(p.intent);
                    setSchema(null);
                    setProposal(null);
                    setPreview(null);
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="mt-4 flex items-center gap-2">
              <Button onClick={propose} disabled={!firstUrl || description.length < 3 || busy !== ""}>
                {busy === "propose" ? <Spinner /> : null} Propose schema
              </Button>
              {proposal ? <span className="text-xs text-muted">{proposal.llm_calls.calls} model call</span> : null}
            </div>
          </Card>
          {schema ? (
            <Card title="2 · Schema (editable)" aside={<span className="text-xs text-muted">{schema.fields.length} fields</span>}>
              <SchemaEditor schema={schema} rejected={proposal?.rejected ?? []} onChange={setSchema} />
              {proposal?.injection_findings.length ? (
                <div className="mt-3 rounded-lg bg-rose-soft px-3 py-2 text-xs text-rose">
                  <b>Prompt injection on the sample page</b>: {proposal.injection_findings.length} sentence(s) addressed to AI systems were quarantined before the model saw the page.
                </div>
              ) : null}
              <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-ink-soft">
                <label className="flex items-center gap-1.5">
                  strategy
                  <select className="rounded border border-line bg-white px-1 py-0.5" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                    <option value="auto">auto</option>
                    <option value="learned">learned rules</option>
                    <option value="direct">direct LLM</option>
                  </select>
                </label>
                <label className="flex items-center gap-1.5">
                  max pages
                  <input type="number" min={1} max={2000} className="w-16 rounded border border-line px-1 py-0.5" value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} />
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={heal} onChange={(e) => setHeal(e.target.checked)} /> self-heal
                </label>
              </div>
              <div className="mt-4 flex gap-2">
                <Button kind="secondary" onClick={runPreview} disabled={busy !== ""}>
                  {busy === "preview" ? <Spinner /> : null} Preview first page
                </Button>
                <Button onClick={run} disabled={busy !== ""}>
                  {busy === "run" ? <Spinner /> : null} Run job
                </Button>
              </div>
            </Card>
          ) : null}
          {error ? <ErrorBox>{error}</ErrorBox> : null}
        </div>
        <div className="min-w-0 space-y-5">
          {preview ? (
            <>
              <Card
                title="3 · Preview with provenance"
                aside={
                  <div className="flex items-center gap-2 text-xs text-muted">
                    {preview.stats.strategies.map((s) => (
                      <StrategyPill key={s} strategy={s} />
                    ))}
                    <span>{preview.records.length} records</span>
                    <span>· {preview.stats.llm.calls} model call(s)</span>
                  </div>
                }
                pad={false}
              >
                <div className="px-4 pt-3 text-xs text-muted">Hover a cell to see the page, the rule and the raw text behind the value.</div>
                <div className="px-2 pt-2">
                  {preview.records.length && schema ? <RecordTable records={preview.records} fields={schema.fields} pinned={{ row: 1, field: schema.fields[1]?.name ?? schema.fields[0].name }} /> : <div className="p-6 text-sm text-muted">No records on this page{preview.error ? `: ${preview.error}` : ""}.</div>}
                </div>
              </Card>
              {program ? (
                <Card title="Learned extractor" aside={strategyEvent ? <span className="max-w-[60%] truncate text-xs text-muted">{strategyEvent.message}</span> : null}>
                  <div className="mono grid gap-x-4 gap-y-1 text-[12px] md:grid-cols-[150px_1fr]">
                    <span className="text-muted">item</span>
                    <span className="text-iris">{program.item}</span>
                    {Object.entries(program.fields).map(([name, rule]) => (
                      <div key={name} className="contents">
                        <span className="text-muted">{name}</span>
                        <span className="break-all text-ink">{ruleText(rule)}</span>
                      </div>
                    ))}
                    <span className="text-muted">next page</span>
                    <span className="text-ink">{ruleText(program.next_page)}</span>
                    {program.missing?.length ? (
                      <>
                        <span className="text-muted">from detail pages</span>
                        <span className="text-teal">{program.missing.join(", ")}</span>
                      </>
                    ) : null}
                  </div>
                  <p className="mt-3 text-xs text-muted">These rules run on every other page of this template with no model call. If the site changes its layout, Sift notices (records disappear, fields go empty) and re-learns them, checking the new rules against records it already has.</p>
                </Card>
              ) : null}
            </>
          ) : (
            <Card>
              <div className="grid-bg flex min-h-[420px] flex-col items-center justify-center rounded-lg text-center">
                <div className="max-w-md text-sm text-ink-soft">
                  <div className="mb-2 text-lg font-semibold text-ink">From a sentence to validated records</div>
                  <ol className="space-y-1 text-left text-[13px]">
                    <li>
                      <Pill tone="iris">1</Pill> A model proposes typed fields (money with currency, dates, enums, URLs, lists) that you can edit.
                    </li>
                    <li>
                      <Pill tone="iris">2</Pill> It writes CSS/XPath rules from a sample page; code checks them on several pages.
                    </li>
                    <li>
                      <Pill tone="iris">3</Pill> Every value is normalized and validated in code, with its source.
                    </li>
                    <li>
                      <Pill tone="teal">4</Pill> The crawl runs politely (robots.txt, 1 request/s per site) without a model call per page.
                    </li>
                  </ol>
                </div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </Page>
  );
}

export default function Home() {
  return (
    <Suspense>
      <Builder />
    </Suspense>
  );
}
