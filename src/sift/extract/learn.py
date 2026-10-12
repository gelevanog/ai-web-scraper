"""Strategy A: learn an extractor from a few sample pages, validate it in code, retry with feedback.

One model call writes the rules and, in the same answer, the values it expects for the first two records. Code then
runs the rules on every sample page and checks them: the item selector matches records on each page, each field is
filled and normalizes to its type, the rules reproduce the model's own examples, the records are not all identical,
and (when healing) the rules reproduce records extracted from this site before its layout changed. Failures go back
to the model as concrete feedback ("price: selector returned nothing in 12 of 12 items") for up to
`max_attempts` attempts; the best attempt is kept and marked unaccepted if none passed."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sift.extract.dom import HtmlElement, page_lang, skeleton, strip_hidden
from sift.extract.program import Program, ProgramError, parse_program, raw_values, run_detail, run_list
from sift.extract.prompts import LEARN, LEARN_DETAIL
from sift.llm.base import ChatModel, LLMError
from sift.llm.jsonparse import JSONReplyError, parse_json_object
from sift.normalize.dates import day_first_for_locale
from sift.schema.model import FieldType, Schema
from sift.validate.injection import Finding, guard_dom, spotlight
from sift.validate.normalize import Context, FieldResult, normalize_record
from sift.validate.quality import norm_key, record_key


@dataclass
class Sample:
    url: str
    doc: HtmlElement


@dataclass
class Check:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0
    items_per_page: list[int] = field(default_factory=list)
    fill: dict[str, float] = field(default_factory=dict)
    agreement: float | None = None
    reference_agreement: float | None = None


@dataclass
class LearnResult:
    program: Program | None
    accepted: bool
    attempts: int
    calls: int
    check: Check | None
    history: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)


ContextFactory = Callable[[str, HtmlElement], Context]


def typed(record: dict[str, FieldResult]) -> dict[str, Any]:
    return {name: result.value for name, result in record.items()}


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict) and "amount" in a and "amount" in b:
        return abs(float(a["amount"]) - float(b["amount"])) <= 0.011
    if (
        isinstance(a, float | int)
        and isinstance(b, float | int)
        and not isinstance(a, bool)
        and not isinstance(b, bool)
    ):
        return abs(float(a) - float(b)) <= max(0.011, abs(float(b)) * 0.001)
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, (norm_key(x) for x in a))) == sorted(map(str, (norm_key(x) for x in b)))
    if isinstance(a, str) and isinstance(b, str):
        return norm_key(a) == norm_key(b)
    return bool(a == b)


def run_program_on(sample: Sample, program: Program, schema: Schema, ctx: Context) -> list[dict[str, FieldResult]]:
    clean, _ = strip_hidden(sample.doc)
    if program.kind == "detail":
        raw = run_detail(clean, program)
        return [normalize_record(raw_values(raw, schema), schema, ctx)]
    return [normalize_record(raw_values(raw, schema), schema, ctx) for raw in run_list(clean, program)]


def check_program(
    program: Program,
    schema: Schema,
    samples: list[Sample],
    ctx_for: ContextFactory,
    *,
    examples: list[dict[str, Any]] | None = None,
    reference: list[dict[str, Any]] | None = None,
) -> Check:
    check = Check(ok=True)
    wanted = [f for f in schema.fields if f.name not in program.missing]
    if program.kind == "list" and not program.item:
        return Check(ok=False, errors=["no item selector"])
    all_records: list[dict[str, FieldResult]] = []
    for sample in samples:
        try:
            records = run_program_on(sample, program, schema, ctx_for(sample.url, sample.doc))
        except ProgramError as exc:
            return Check(ok=False, errors=[str(exc)])
        check.items_per_page.append(len(records))
        if not records:
            check.errors.append(f"the item selector {program.item!r} matched nothing on {sample.url}")
        all_records.extend(records)
    if not all_records:
        check.ok = False
        return check
    for spec in wanted:
        if spec.name not in program.fields:
            if spec.required:
                check.errors.append(f"{spec.name}: no rule given (required field)")
            else:
                check.warnings.append(f"{spec.name}: no rule given")
            check.fill[spec.name] = 0.0
            continue
        filled = sum(1 for r in all_records if r[spec.name].value not in (None, "", []))
        raw_unparsed = [
            r[spec.name].raw
            for r in all_records
            if r[spec.name].raw not in (None, "", []) and r[spec.name].value in (None, [])
        ]
        ratio = filled / len(all_records)
        check.fill[spec.name] = round(ratio, 3)
        rule = program.fields[spec.name].describe()
        if raw_unparsed:
            sample_raw = str(raw_unparsed[0])[:80]
            message = f"{spec.name}: rule `{rule}` returned text that is not a valid {spec.type.value} in {len(raw_unparsed)} of {len(all_records)} items, e.g. {sample_raw!r}"
            # A few unparseable values are often legitimate blanks written as words ("No reviews yet").
            if len(raw_unparsed) > max(1, (filled + len(raw_unparsed)) // 3):
                check.errors.append(message)
            else:
                check.warnings.append(message)
        if ratio == 0 and not raw_unparsed:
            check.errors.append(f"{spec.name}: rule `{rule}` found nothing in any of {len(all_records)} items")
        elif ratio > 0 and spec.required and ratio < 0.9:
            check.errors.append(
                f"{spec.name}: rule `{rule}` is empty in {len(all_records) - filled} of {len(all_records)} items (required field)"
            )
        elif 0 < ratio < 0.5 and spec.type not in {FieldType.list}:
            check.warnings.append(f"{spec.name}: empty in {len(all_records) - filled} of {len(all_records)} items")
    keys = [record_key(typed(r), schema) for r in all_records]
    if len(all_records) >= 4 and len(set(keys)) <= len(all_records) / 3:
        check.errors.append(
            f"records are mostly identical ({len(set(keys))} distinct of {len(all_records)}): the item selector matches the wrong element"
        )
    if examples:
        first = [typed(r) for r in all_records[: len(examples)]]
        ctx0 = ctx_for(samples[0].url, samples[0].doc)
        agree = total = 0
        for got, raw_expected in zip(first, examples, strict=False):
            expected = typed(normalize_record(raw_expected, schema, ctx0))
            for spec in wanted:
                exp = expected.get(spec.name)
                if exp in (None, "", []):
                    continue
                total += 1
                if _values_equal(got.get(spec.name), exp):
                    agree += 1
                else:
                    check.warnings.append(
                        f"{spec.name}: your example says {exp!r} but the rule returned {got.get(spec.name)!r}"
                    )
        check.agreement = agree / total if total else None
        if check.agreement is not None and check.agreement < 0.75:
            check.errors.append(f"the rules reproduce only {agree} of {total} example values")
    if reference:
        by_key = {record_key(r, schema): r for r in reference}
        matched = agree = total = 0
        misses: dict[str, list[tuple[Any, Any]]] = {}
        compared: dict[str, int] = {}
        for record in all_records:
            data = typed(record)
            ref = by_key.get(record_key(data, schema))
            if ref is None:
                continue
            matched += 1
            for spec in wanted:
                want = ref.get(spec.name)
                # Empty values and text the PII guard rewrote are not evidence either way.
                if want in (None, "", []) or (isinstance(want, str) and "removed]" in want):
                    continue
                total += 1
                compared[spec.name] = compared.get(spec.name, 0) + 1
                if _values_equal(data.get(spec.name), want):
                    agree += 1
                else:
                    misses.setdefault(spec.name, []).append((data.get(spec.name), want))
        check.reference_agreement = agree / total if total else 0.0
        # Overall agreement can hide one completely broken field, so each field is held to a bar too.
        weak = {
            name: pairs
            for name, pairs in misses.items()
            if compared.get(name, 0) >= 5 and len(pairs) / compared[name] > 0.2
        }
        if matched == 0:
            check.errors.append(
                "none of the records matches a record extracted before the layout change (check the name/title rule)"
            )
        else:
            if check.reference_agreement < 0.9:
                check.errors.append(
                    f"only {agree} of {total} values agree with records extracted before the layout change"
                )
            if check.reference_agreement < 0.9 or weak:
                for name, pairs in sorted((weak or misses).items(), key=lambda kv: -len(kv[1]))[:4]:
                    got, want = pairs[0]
                    check.errors.append(
                        f"{name}: {len(pairs)} of {compared.get(name, len(pairs))} value(s) differ from earlier records, e.g. the rule gave {got!r} where the earlier record has {want!r}"
                    )
    fill_mean = sum(check.fill.values()) / max(len(check.fill), 1)
    agreement = (
        check.reference_agreement
        if check.reference_agreement is not None
        else (check.agreement if check.agreement is not None else 1.0)
    )
    check.score = round(fill_mean * agreement * (0.5 if check.errors else 1.0), 4)
    check.ok = not check.errors
    return check


def _prompt(
    schema: Schema,
    samples: list[Sample],
    *,
    kind: str,
    fields: list[str] | None,
    previous: Program | None,
    reference: list[dict[str, Any]] | None,
    feedback: list[str],
    guard: bool,
) -> tuple[str, str, list[Finding]]:
    findings: list[Finding] = []
    pages = []
    for sample in samples[:1]:
        doc = sample.doc
        if guard:
            doc, found = guard_dom(doc)
            findings.extend(found)
        html = skeleton(doc, max_chars=26_000 if kind == "list" else 14_000)
        pages.append(f"Sample page: {sample.url}\n{spotlight(html)}")
    wanted = [f for f in schema.fields if fields is None or f.name in fields]
    lines = "\n".join(f"- {f.short()}" for f in wanted)
    compact = Schema(name=schema.name, fields=wanted).model_dump_json(exclude_defaults=True)
    parts = [f"Schema fields:\n{lines}\nSchema JSON: {compact}"]
    if previous is not None:
        parts.append(
            "This site changed its layout and the rules below stopped working. Write new rules for the new layout.\n"
            f"Old rules (for reference only):\n{previous.model_dump_json(exclude_none=True, exclude_defaults=True)[:3000]}"
        )
    if reference:
        shown = [{k: v for k, v in r.items() if v not in (None, "", [])} for r in reference[:4]]
        parts.append(
            "Records extracted from this site before the change (the same data should be on the new pages):\n"
            + json.dumps(shown, ensure_ascii=False)[:2500]
        )
    if len(samples) > 1:
        parts.append(f"The rules must also work on {len(samples) - 1} more page(s) of the same template.")
    parts.extend(pages)
    if feedback:
        parts.append("Your previous rules failed these checks; fix them:\n- " + "\n- ".join(feedback[:12]))
    system = (
        LEARN.format(task="heal_extractor" if previous is not None else "learn_extractor")
        if kind == "list"
        else LEARN_DETAIL.format()
    )
    return system, "\n\n".join(parts), findings


def learn(
    model: ChatModel,
    schema: Schema,
    samples: list[Sample],
    ctx_for: ContextFactory,
    *,
    kind: str = "list",
    fields: list[str] | None = None,
    previous: Program | None = None,
    reference: list[dict[str, Any]] | None = None,
    max_attempts: int = 3,
    max_tokens: int = 16_000,
    guard: bool = True,
) -> LearnResult:
    if not samples:
        return LearnResult(None, False, 0, 0, None)
    best: tuple[float, Program | None, Check | None, list[dict[str, Any]]] = (-1.0, None, None, [])
    feedback: list[str] = []
    history: list[dict[str, Any]] = []
    findings: list[Finding] = []
    calls = 0
    sub_schema = (
        Schema(name=schema.name, fields=[f for f in schema.fields if fields is None or f.name in fields])
        if fields
        else schema
    )
    for attempt in range(1, max_attempts + 1):
        system, user, found = _prompt(
            schema,
            samples,
            kind=kind,
            fields=fields,
            previous=previous,
            reference=reference,
            feedback=feedback,
            guard=guard,
        )
        if attempt == 1:
            findings.extend(found)
        calls += 1
        try:
            reply = model.complete(
                [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=max_tokens
            )
            data = parse_json_object(reply.text)
            examples = [e for e in (data.get("examples") or []) if isinstance(e, dict)][:2]
            program = parse_program({**data, "kind": kind})
        except (JSONReplyError, ProgramError) as exc:
            feedback = [f"your answer was not valid: {str(exc)[:200]}"]
            history.append({"attempt": attempt, "errors": feedback})
            continue
        except LLMError as exc:
            history.append({"attempt": attempt, "errors": [f"model error: {str(exc)[:200]}"]})
            break
        if kind == "detail":
            program.item = None
        check = check_program(program, sub_schema, samples, ctx_for, examples=examples, reference=reference)
        if previous is not None and kind == "list":
            # A field the old rules read from these pages is still on them after a redesign (and the earlier records
            # have it): declaring it "missing" would silently lose it.
            lost = [
                name
                for name in program.missing
                if name in previous.fields
                and name not in previous.missing
                and any(r.get(name) not in (None, "", []) for r in reference or [])
            ]
            if lost:
                check.errors.append(
                    f"{', '.join(lost)}: shown on these pages before the redesign (the earlier records have values); write a rule instead of listing it as missing"
                )
                check.ok = False
        history.append(
            {
                "attempt": attempt,
                "errors": check.errors,
                "warnings": check.warnings[:8],
                "score": check.score,
                "items": check.items_per_page,
            }
        )
        if check.score > best[0]:
            best = (check.score, program, check, examples)
        if check.ok:
            return LearnResult(program, True, attempt, calls, check, history, findings, examples)
        feedback = check.errors + check.warnings[:4]
    _, best_program, best_check, best_examples = best
    return LearnResult(best_program, False, len(history), calls, best_check, history, findings, best_examples)


def default_context_factory(today: Any, fx: Any, default_currency: str | None = None) -> ContextFactory:
    def make(url: str, doc: HtmlElement) -> Context:
        return Context(
            page_url=url,
            today=today,
            fx=fx,
            day_first=day_first_for_locale(page_lang(doc)),
            default_currency=default_currency,
        )

    return make
