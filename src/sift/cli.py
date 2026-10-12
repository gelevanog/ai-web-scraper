"""sift: describe the data you want, get clean structured JSON from any website.

sift seed                                   generate the synthetic web (47 fictional sites, v1 and v2 layouts)
sift schema URL "every product with ..."    propose a typed schema from a request and a sample page
sift learn URL --schema schema.json         learn an extractor (selectors) from sample pages
sift extract URL --extractor ext.json       run a learned extractor on a page (no model call)
sift crawl URL... -d "..." [--schema f]     run a whole job: crawl, extract, validate, store
sift heal JOB_ID [--layout v2]              re-crawl a job (unchanged pages skipped, layout changes healed)
sift export JOB_ID -f csv -o out.csv        export (json, jsonl, csv, parquet), --webhook URL, --table
sift eval SUITE                             evaluation suites (see `sift eval --help`)
sift serve / sift worker                    REST API and job worker
sift mcp [--http --port 8765]               MCP server for Claude Code and Claude Desktop (stdio by default)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from sift.config import Settings, get_settings
from sift.logging_config import configure_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
console = Console(stderr=True)
out = Console()


@app.callback()
def main(log_level: Annotated[str, typer.Option("--log-level", help="DEBUG, INFO, WARNING")] = "WARNING") -> None:
    configure_logging(log_level)


def _emit(value: Any, **kwargs: Any) -> None:
    """Machine-readable JSON on stdout (not wrapped or coloured)."""
    kwargs.setdefault("default", str)
    typer.echo(
        json.dumps(value, indent=1, ensure_ascii=False, **{k: v for k, v in kwargs.items() if k != "ensure_ascii"})
    )


def _settings(**overrides: Any) -> Settings:
    base = get_settings()
    return base.model_copy(update={k: v for k, v in overrides.items() if v is not None}) if overrides else base


def _schema_arg(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    inner = data.get("schema", data)
    return inner if isinstance(inner, dict) else None


@app.command()
def seed(
    seed: Annotated[int, typer.Option(help="generator seed")] = 7,
    out_dir: Annotated[Path | None, typer.Option("--out")] = None,
) -> None:
    """Generate the synthetic web and its ground truth."""
    from sift.synthetic.generator import generate

    settings = get_settings()
    manifest = generate(out_dir or settings.synthetic_web_dir, seed=seed, today=settings.crawl_today())
    _emit(manifest)


@app.command()
def schema(
    url: str,
    description: str,
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
) -> None:
    """Propose a typed schema from a plain-English request and a sample page."""
    from sift.services import Services

    result = Services(get_settings()).propose_schema(url, description)
    if output and result.get("schema"):
        output.write_text(json.dumps(result["schema"], indent=1, ensure_ascii=False), encoding="utf-8")
    _emit(result, ensure_ascii=False, default=str)
    if not result.get("schema"):
        raise typer.Exit(1)


@app.command()
def learn(
    url: str,
    schema_file: Annotated[Path, typer.Option("--schema", help="schema JSON (from `sift schema -o`)")],
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    pages: Annotated[int, typer.Option(help="sample pages (the start page plus pagination)")] = 3,
) -> None:
    """Learn an extractor (selectors + transforms) from sample pages and validate it."""
    from sift.crawl.discovery import pagination_links
    from sift.engine import Engine, JobConfig
    from sift.extract.learn import Sample
    from sift.extract.learn import learn as learn_fn
    from sift.llm.factory import model_for
    from sift.schema.model import Schema

    settings = get_settings()
    spec = Schema.model_validate(_schema_arg(schema_file))
    engine = Engine(settings, model_for(settings, tag="cli:learn"))
    job = JobConfig(start_urls=[url])
    try:
        if not engine.http.allowed(url):
            console.print(f"[red]robots.txt disallows {url}[/red]")
            raise typer.Exit(1)
        _, doc, _ = engine.fetch(url, job, {})
        if doc is None:
            console.print("[red]not an HTML page[/red]")
            raise typer.Exit(1)
        samples = [Sample(url, doc)]
        for link in pagination_links(doc, url)[: pages - 1]:
            if engine.http.allowed(link) and (fetched := engine.fetch(link, job, {}))[1] is not None:
                samples.append(Sample(link, fetched[1]))
        result = learn_fn(
            engine.model,
            spec,
            samples,
            engine._ctx_factory(job),
            max_attempts=settings.learn_max_attempts,
            max_tokens=settings.llm_max_tokens,
        )
    finally:
        engine.close()
    payload = {
        "accepted": result.accepted,
        "attempts": result.attempts,
        "calls": result.calls,
        "errors": result.check.errors if result.check else [],
        "warnings": result.check.warnings if result.check else [],
        "extractor": result.program.model_dump(mode="json", exclude_none=True) if result.program else None,
    }
    if output and result.program is not None:
        output.write_text(
            json.dumps(
                {"schema": spec.model_dump(mode="json", exclude_defaults=True), "extractor": payload["extractor"]},
                indent=1,
            ),
            encoding="utf-8",
        )
    _emit(payload, ensure_ascii=False)
    if not result.accepted:
        raise typer.Exit(2)


@app.command()
def extract(
    url: str,
    extractor_file: Annotated[Path, typer.Option("--extractor", help="file written by `sift learn -o`")],
) -> None:
    """Run a learned extractor on one page: no model call."""
    from sift.engine import Engine, JobConfig
    from sift.extract.learn import Sample, run_program_on, typed
    from sift.extract.program import Program
    from sift.llm.fake import FakeModel
    from sift.schema.model import Schema

    data = json.loads(extractor_file.read_text(encoding="utf-8"))
    spec = Schema.model_validate(data["schema"])
    program = Program.model_validate(data["extractor"])
    settings = get_settings()
    engine = Engine(settings, FakeModel({}))
    job = JobConfig(start_urls=[url])
    try:
        if not engine.http.allowed(url):
            console.print(f"[red]robots.txt disallows {url}[/red]")
            raise typer.Exit(1)
        _, doc, _ = engine.fetch(url, job, {})
    finally:
        engine.close()
    if doc is None:
        raise typer.Exit(1)
    records = [typed(r) for r in run_program_on(Sample(url, doc), program, spec, engine._ctx(url, doc, None))]
    for record in records:
        typer.echo(json.dumps(record, ensure_ascii=False))
    console.print(f"{len(records)} records, 0 model calls")


def _print_job(job: dict[str, Any]) -> None:
    stats = job.get("stats") or {}
    llm = stats.get("llm") or {}
    console.print(
        f"[bold]{job['id']}[/bold] {job['status']}: {stats.get('records', 0)} records from {stats.get('pages', 0)} pages, "
        f"{llm.get('calls', 0)} model call(s), {stats.get('seconds', 0)} s"
    )


@app.command()
def crawl(
    urls: list[str],
    description: Annotated[str, typer.Option("-d", "--description")] = "",
    schema_file: Annotated[Path | None, typer.Option("--schema")] = None,
    strategy: Annotated[str, typer.Option(help="auto | learned | direct")] = "auto",
    max_pages: Annotated[int, typer.Option()] = 60,
    layout: Annotated[str | None, typer.Option(help="synthetic web layout: v1 | v2")] = None,
    output: Annotated[Path | None, typer.Option("-o", "--output", help="write records as JSON Lines")] = None,
) -> None:
    """Run a job now (crawl, extract, validate, store) and print a summary."""
    from sift.services import JobRequest, Services

    svc = Services(get_settings())
    request = JobRequest.model_validate(
        {
            "start_urls": urls,
            "description": description,
            "schema": _schema_arg(schema_file),
            "strategy": strategy,
            "max_pages": max_pages,
            "synthetic_layout": layout,
        }
    )
    job_id = svc.submit(request)
    svc.store.update_job(job_id, status="running")
    svc.run(job_id)
    job = svc.store.get_job(job_id) or {}
    _print_job(job)
    if output:
        body, _ = svc.export(job_id, "jsonl")
        output.write_bytes(body)
    if job.get("status") != "done":
        console.print(f"[red]{job.get('error')}[/red]")
        raise typer.Exit(1)


@app.command()
def heal(
    job_id: str,
    layout: Annotated[str | None, typer.Option(help="synthetic web layout for the re-crawl: v1 | v2")] = None,
) -> None:
    """Re-crawl a job: reuse its extractors, skip unchanged pages, detect layout changes and heal."""
    from sift.services import Services

    svc = Services(get_settings())
    new_id = svc.recrawl(job_id, synthetic_layout=layout)
    svc.store.update_job(new_id, status="running")
    svc.run(new_id)
    job = svc.store.get_job(new_id) or {}
    _print_job(job)
    for event in svc.store.events(job_id=new_id, kinds=["layout_changed", "healed", "heal_failed"]):
        console.print(f"  {event['kind']}: {event['message']}")


@app.command()
def jobs(limit: int = 20) -> None:
    """List recent jobs."""
    from sift.services import Services

    table = Table("status", "name", "records", "pages", "model calls", "created")
    table.add_column("id", no_wrap=True, min_width=15)
    for job in Services(get_settings()).store.list_jobs(limit):
        stats = job.get("stats") or {}
        table.add_row(
            job["status"],
            job["name"][:50],
            str(stats.get("records", "")),
            str(stats.get("pages", "")),
            str((stats.get("llm") or {}).get("calls", "")),
            job["created_at"][:19],
            job["id"],
        )
    out.print(table)


@app.command()
def export(
    job_id: str,
    fmt: Annotated[str, typer.Option("-f", "--format", help="json | jsonl | csv | parquet")] = "jsonl",
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    webhook: Annotated[str | None, typer.Option(help="POST the records to this URL")] = None,
    table: Annotated[bool, typer.Option("--table", help="upsert into a typed table in SIFT_DATABASE_URL")] = False,
) -> None:
    """Export a job's records to a file, a webhook or a database table."""
    from sift.exports import send_webhook, to_database
    from sift.schema.model import Schema
    from sift.services import Services

    settings = get_settings()
    svc = Services(settings)
    job = svc.store.get_job(job_id)
    if job is None or not job.get("schema"):
        console.print(f"[red]no finished job {job_id}[/red]")
        raise typer.Exit(1)
    if webhook:
        console.print(
            send_webhook(webhook, job, svc.store.records(job_id, limit=1_000_000), secret=settings.webhook_secret)
        )
    if table:
        console.print(
            to_database(
                svc.store.records(job_id, limit=1_000_000),
                Schema.model_validate(job["schema"]),
                settings.database_url,
                job_id=job_id,
            )
        )
    if (not webhook and not table) or output:
        body, _ = svc.export(job_id, fmt)
        if output:
            output.write_bytes(body)
            console.print(f"wrote {output} ({len(body):,} bytes)")
        else:
            sys.stdout.buffer.write(body)


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int = 8000,
    inline_worker: Annotated[bool, typer.Option("--inline-worker", help="run jobs inside the API process")] = False,
) -> None:
    """Start the REST API (OpenAPI docs at /docs)."""
    import uvicorn

    from sift.api.app import create_app

    uvicorn.run(create_app(inline_worker=inline_worker), host=host, port=port, log_level="warning")


@app.command("mcp")
def mcp_server(
    http: Annotated[bool, typer.Option("--http", help="Streamable HTTP instead of stdio")] = False,
    host: Annotated[str, typer.Option(help="HTTP bind address (local only by default)")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="HTTP port")] = 8765,
) -> None:
    """MCP server for Claude Code and Claude Desktop: polite fetch, extractor testing, saved extractors."""
    from sift.mcp_server import serve as serve_mcp

    if http:
        console.print(f"Sift MCP server on http://{host}:{port}/mcp")
    serve_mcp(http=http, host=host, port=port)


@app.command()
def worker(once: Annotated[bool, typer.Option("--once", help="exit when the queue is empty")] = False) -> None:
    """Run queued jobs."""
    from sift.services import Services
    from sift.worker import worker_loop

    count = asyncio.run(worker_loop(Services(get_settings()), once=once))
    console.print(f"ran {count} job(s)")


@app.command("synthetic-server")
def synthetic_server(port: int = 8090, layout: str = "v1") -> None:
    """Serve the synthetic web over HTTP (Host-header or /<domain>/ path routing)."""
    from sift.synthetic.server import serve as serve_web

    console.print(f"synthetic web on http://localhost:{port}/ (layout {layout})")
    serve_web(get_settings().synthetic_web_dir, port=port, layout=layout)


eval_app = typer.Typer(no_args_is_help=True, help="Evaluation suites. Results go to results/<name>_<suite>.json.")
app.add_typer(eval_app, name="eval")


@eval_app.command("synthetic")
def eval_synthetic(
    name: Annotated[str, typer.Option()] = "offline",
    sites: Annotated[str | None, typer.Option(help="comma-separated site ids (default: all)")] = None,
    strategy: Annotated[str, typer.Option()] = "auto",
    workers: Annotated[int, typer.Option(help="sites in parallel")] = 4,
) -> None:
    """Field accuracy and record P/R on v1, then the v2 redesign before and after healing."""
    from sift.eval.runner import run_synthetic

    summary = run_synthetic(
        get_settings(), name=name, sites=sites.split(",") if sites else None, strategy=strategy, workers=workers
    )
    _emit(summary["aggregate"], default=str)


@eval_app.command("direct")
def eval_direct(
    name: Annotated[str, typer.Option()] = "offline",
    sites: Annotated[str | None, typer.Option()] = None,
    pages_per_site: Annotated[int, typer.Option()] = 1,
    workers: Annotated[int, typer.Option()] = 4,
) -> None:
    """Strategy B (direct LLM extraction) on the same first pages, for the strategy comparison."""
    from sift.eval.runner import run_direct

    summary = run_direct(
        get_settings(),
        name=name,
        sites=sites.split(",") if sites else None,
        pages_per_site=pages_per_site,
        workers=workers,
    )
    _emit(summary["aggregate"], default=str)


@eval_app.command("schema")
def eval_schema(
    name: Annotated[str, typer.Option()] = "offline",
    sites: Annotated[str | None, typer.Option()] = None,
    workers: Annotated[int, typer.Option()] = 4,
) -> None:
    """Schema from intent: requested fields inferred with the right type."""
    from sift.eval.runner import run_schema

    summary = run_schema(get_settings(), name=name, sites=sites.split(",") if sites else None, workers=workers)
    _emit(summary["aggregate"], default=str)


@eval_app.command("traps")
def eval_traps(name: Annotated[str, typer.Option()] = "offline") -> None:
    """Traps: injection (with and without the guard), hidden items, duplicates, currencies, relative dates, PII,
    anomalies, robots.txt (reads the synthetic run's records; runs the injection counterfactuals)."""
    from sift.eval.runner import run_traps

    _emit(run_traps(get_settings(), name=name)["aggregate"], default=str)


@eval_app.command("toscrape")
def eval_toscrape(
    name: Annotated[str, typer.Option()] = "main",
    books_pages: Annotated[int, typer.Option(help="catalogue pages of books.toscrape.com")] = 3,
    quotes_pages: Annotated[int, typer.Option(help="pages of quotes.toscrape.com")] = 10,
) -> None:
    """books.toscrape.com and quotes.toscrape.com (sites built for scraping practice), politely: robots.txt, at
    most one request per second, page caps."""
    from sift.eval.toscrape import run_toscrape

    _emit(run_toscrape(get_settings(), name=name, books_pages=books_pages, quotes_pages=quotes_pages)["aggregate"])


@eval_app.command("free-models")
def eval_free_models() -> None:
    """List the free OpenRouter models (no key needed) into results/free_models.json."""
    from sift.eval.smoke import list_free_models

    _emit(list_free_models(get_settings()))


@eval_app.command("smoke")
def eval_smoke(models: Annotated[str, typer.Option(help="comma-separated :free model ids")]) -> None:
    """One learning call and one direct extraction per model, to pick models before a run."""
    from sift.eval.smoke import smoke

    _emit(smoke(get_settings(), models.split(",")), default=str)


@eval_app.command("summary")
def eval_summary() -> None:
    """Fold results/*.json and the call ledger into results/summary.json (the dashboard's evaluation page)."""
    from sift.eval.summary import build_summary

    summary = build_summary(get_settings())
    _emit({k: summary.get(k) for k in ("synthetic", "strategies", "ledger")}, default=str)


if __name__ == "__main__":
    app()
