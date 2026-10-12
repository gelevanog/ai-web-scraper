from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sift.extract.dom import fingerprint, parse, skeleton, strip_hidden, text_of
from sift.extract.learn import Sample, check_program, default_context_factory, learn, run_program_on, typed
from sift.extract.program import Program, ProgramError, parse_program, run_detail, run_list
from sift.fake_tasks import HANDLERS
from sift.llm.base import Completion, Message
from sift.llm.fake import FakeModel
from sift.normalize.money import DEFAULT_RATES, FxTable
from sift.schema.model import FieldSpec, FieldType, Schema
from sift.synthetic.render import url_to_storage

FX = FxTable(dict(DEFAULT_RATES))
CTX = default_context_factory(dt.date(2026, 10, 1), FX)

PAGE = """<html lang="en-US"><body>
<section><h2>Starters</h2><ul>
 <li class="dish"><span class="name">Leek Soup</span> <abbr title="Vegan">VG</abbr><span class="price">$9.50</span></li>
 <li class="dish"><span class="name">Calamari</span><span class="price">$12.00</span></li>
</ul></section>
<section><h2>Mains</h2><ul>
 <li class="dish"><span class="name">Short Rib</span><span class="price">$24.00</span></li>
 <li class="dish" hidden><span class="name">Secret Dish</span><span class="price">$1.00</span></li>
</ul></section>
<div class="stars"><i class="on"></i><i class="on"></i><i class="on"></i><i></i></div>
<ul class="sizes"><li>S</li><li class="soldout"><s>M</s></li></ul>
<dl><dt>SKU</dt><dd>ABC-123</dd></dl>
<a rel="next" href="/menu/?page=2">Next</a>
</body></html>"""

MENU = Schema(
    name="dish",
    fields=[
        FieldSpec(name="dish", type=FieldType.string, required=True, key=True),
        FieldSpec(name="section", type=FieldType.string),
        FieldSpec(name="price", type=FieldType.money, currency="USD", required=True),
        FieldSpec(
            name="dietary",
            type=FieldType.list,
            items=FieldSpec(name="t", type=FieldType.enum, enum=["vegan", "vegetarian"]),
        ),
    ],
)


def test_program_runs_css_xpath_attributes_and_lists() -> None:
    doc, _ = strip_hidden(parse(PAGE))
    program = parse_program(
        {
            "item": "li.dish",
            "fields": {
                "dish": ".name",
                "section": {"xpath": "./ancestor::section[1]/h2"},
                "price": {"css": ".price", "regex": r"\$([\d.]+)"},
                "dietary": {"css": "abbr", "attr": "title", "all": True},
            },
            "next_page": {"css": "a[rel=next]", "attr": "href"},
        }
    )
    raws = run_list(doc, program)
    assert [r.values["dish"].value for r in raws] == ["Leek Soup", "Calamari", "Short Rib"]  # the hidden dish is gone
    assert [r.values["section"].value for r in raws] == ["Starters", "Starters", "Mains"]
    assert raws[0].values["price"].value == "9.50" and raws[0].values["dietary"].value == ["Vegan"]
    assert raws[0].values["dish"].node.endswith("span[1]")


def test_count_exists_negate_value_map_fallback_and_objects() -> None:
    doc = parse(PAGE)
    rules = parse_program(
        {
            "fields": {
                "rating": {"css": ".stars i.on", "attr": "count"},
                "has_sizes": {"css": ".sizes", "attr": "exists"},
                "sku": {"xpath": "//dt[normalize-space()='SKU']/following-sibling::dd[1]"},
                "missing": {"css": ".nope", "fallback": {"css": "dt"}},
                "variants": {
                    "css": ".sizes li",
                    "all": True,
                    "fields": {"name": {"css": ""}, "in_stock": {"css": "s", "attr": "exists", "negate": True}},
                },
                "label": {"css": "h2", "value_map": {"Starters": "starter"}},
            }
        }
    )
    rules.kind = "detail"
    record = run_detail(doc, rules)
    values = {k: v.value for k, v in record.values.items()}
    assert (
        values["rating"] == 3
        and values["has_sizes"] is True
        and values["sku"] == "ABC-123"
        and values["missing"] == "SKU"
    )
    assert values["variants"] == [{"name": "S", "in_stock": True}, {"name": "M", "in_stock": False}]
    assert values["label"] == "starter"


def test_bad_programs_are_rejected() -> None:
    with pytest.raises(ProgramError):
        run_list(parse(PAGE), Program(item="li[[["))
    with pytest.raises(ProgramError):
        run_list(parse(PAGE), Program(item=None))
    with pytest.raises(ValueError):
        parse_program({"item": "li", "fields": {"x": {"css": "a", "regex": "("}}})


def test_skeleton_collapses_repeats_and_drops_hidden() -> None:
    items = "".join(
        f'<li class="card"><a href="/p/{i}">Item {i}</a><span class="price">${i}.00</span></li>' for i in range(20)
    )
    html = f"<html><head><script>var x=1</script></head><body><ul>{items}</ul><div hidden>secret</div><p style='display:none'>also secret</p></body></html>"
    out = skeleton(parse(html))
    assert out.count('<li class="card"><a') == 3
    assert "17 more <li" in out
    assert "secret" not in out and "var x" not in out


def test_text_of_skips_footnote_markers() -> None:
    assert (
        text_of(parse("<table><tr><td>Northside Library<sup>1</sup></td></tr></table>").cssselect("td")[0])
        == "Northside Library"
    )


def test_fingerprint_same_template_vs_redesign(site: Any, web_root: Path) -> None:
    shop = site("shop-01")
    host = shop["start_url"].split("/")[2]

    def doc(layout: str, query: str = "") -> Any:
        return parse((web_root / layout / host / url_to_storage(shop["start_url"] + query)).read_bytes())

    v1_p1, v1_p2, v2_p1 = fingerprint(doc("v1")), fingerprint(doc("v1", "?page=2")), fingerprint(doc("v2"))
    assert v1_p1.similarity(v1_p2) > 0.8
    assert v1_p1.similarity(v2_p1) < 0.5
    assert v1_p1.digest() == fingerprint(doc("v1")).digest()


class ScriptedModel:
    """Answers learning prompts from a list, recording what it was asked."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    @property
    def label(self) -> str:
        return "scripted/test"

    @property
    def is_local(self) -> bool:
        return True

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        self.prompts.append(messages[-1]["content"])
        return Completion(json.dumps(self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]), "scripted/test")


def test_learning_retries_with_feedback_until_valid() -> None:
    wrong = {
        "item": "li.dish",
        "fields": {"dish": ".name", "price": ".nope", "section": "h2"},
        "examples": [{"dish": "Leek Soup", "price": "$9.50"}],
    }
    right = {
        "item": "li.dish",
        "fields": {
            "dish": ".name",
            "price": ".price",
            "section": {"xpath": "./ancestor::section[1]/h2"},
            "dietary": {"css": "abbr", "attr": "title", "all": True},
        },
        "examples": [
            {"dish": "Leek Soup", "price": "$9.50", "section": "Starters"},
            {"dish": "Calamari", "price": "$12.00"},
        ],
    }
    model = ScriptedModel([wrong, right])
    result = learn(model, MENU, [Sample("https://r.example/menu/", parse(PAGE))], CTX, max_attempts=3)
    assert result.accepted and result.attempts == 2 and result.calls == 2
    assert "price: rule `css .nope` found nothing" in model.prompts[1]
    assert result.check is not None and result.check.agreement == 1.0
    records = [
        typed(r)
        for r in run_program_on(
            Sample("https://r.example/menu/", parse(PAGE)),
            result.program,
            MENU,
            CTX("https://r.example/menu/", parse(PAGE)),
        )
    ]  # type: ignore[arg-type]
    assert records[0] == {
        "dish": "Leek Soup",
        "section": "Starters",
        "price": {"amount": 9.5, "currency": "USD"},
        "dietary": ["vegan"],
    }


def test_learning_keeps_the_best_attempt_when_none_passes() -> None:
    bad = {"item": "li.dish", "fields": {"dish": ".name", "price": ".nope"}}
    result = learn(ScriptedModel([bad]), MENU, [Sample("https://r.example/menu/", parse(PAGE))], CTX, max_attempts=2)
    assert not result.accepted and result.program is not None and result.attempts == 2


def test_check_against_reference_records() -> None:
    program = parse_program({"item": "li.dish", "fields": {"dish": ".name", "price": ".price"}})
    samples = [Sample("https://r.example/menu/", parse(PAGE))]
    good = [
        {"dish": "Leek Soup", "price": {"amount": 9.5, "currency": "USD"}},
        {"dish": "Calamari", "price": {"amount": 12.0, "currency": "USD"}},
    ]
    assert check_program(program, MENU, samples, CTX, reference=good).ok
    bad = [
        {"dish": "Leek Soup", "price": {"amount": 99.0, "currency": "USD"}},
        {"dish": "Calamari", "price": {"amount": 1.0, "currency": "USD"}},
    ]
    check = check_program(program, MENU, samples, CTX, reference=bad)
    assert not check.ok and any("agree with records extracted before" in e for e in check.errors)


def test_offline_model_learns_a_synthetic_catalog(site: Any, web_root: Path) -> None:
    shop = site("shop-04")
    host = shop["start_url"].split("/")[2]
    doc = parse((web_root / "v1" / host / url_to_storage(shop["start_url"])).read_bytes())
    schema = Schema.model_validate(shop["schema"])
    result = learn(FakeModel(HANDLERS), schema, [Sample(shop["start_url"], doc)], CTX, max_attempts=1)
    assert result.accepted and result.program is not None
    records = [
        typed(r)
        for r in run_program_on(Sample(shop["start_url"], doc), result.program, schema, CTX(shop["start_url"], doc))
    ]
    assert len(records) == 12
    assert records[0]["name"] == shop["records"][0]["name"]
    assert records[0]["price"]["amount"] == shop["records"][0]["price"]


def test_regex_cuts_are_repaired_from_the_element_text() -> None:
    from sift.extract.program import RawRecord, RawValue, Rule, _transform, raw_values

    schema = Schema(
        fields=[
            FieldSpec(name="area", type=FieldType.number, unit="sqm"),
            FieldSpec(name="low", type=FieldType.money),
            FieldSpec(name="eur", type=FieldType.money),
        ]
    )
    record = RawRecord(
        values={
            "area": RawValue("1,200", context="3 bd · 1,200 sq ft"),
            "low": RawValue("85", context="$85k – $110k"),
            "eur": RawValue("60.000", context="60.000 € – 75.000 € per year"),
        }
    )
    assert raw_values(record, schema) == {"area": "1,200 sq ft", "low": "85k USD", "eur": "60.000 EUR"}
    assert (
        _transform("Salary $85k – $110k", Rule(css="x", regex="\\\\$(\\\\d+k)")) == "85k"
    )  # double-escaped by a model


def test_skeleton_keeps_every_table_column() -> None:
    row = "<tr>" + "".join(f"<td>c{i}</td>" for i in range(7)) + "</tr>"
    out = skeleton(parse(f"<html><body><table><tbody>{row * 10}</tbody></table></body></html>"))
    assert "c6" in out and out.count("<tr>") == 3


def test_value_map_prefers_the_longest_matching_key() -> None:
    from sift.extract.program import Rule, _transform

    rule = Rule(css="x", value_map={"Available": "in_stock", "Available for preorder": "preorder"})
    assert _transform("Available for preorder", rule) == "preorder"
    assert _transform("Available now", rule) == "in_stock"


def test_one_broken_field_fails_verification_even_when_overall_agreement_is_high() -> None:
    items = "".join(
        f'<li class="d"><b>{n}</b><i>${p}.00</i><u>{p * 2}</u></li>'
        for n, p in [("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5), ("F", 6)]
    )
    page = parse(f"<html><body><ul>{items}</ul></body></html>")
    schema = Schema(
        fields=[
            FieldSpec(name="n", type=FieldType.string, required=True, key=True),
            FieldSpec(name="price", type=FieldType.money),
            FieldSpec(name="x", type=FieldType.integer),
            FieldSpec(name="y", type=FieldType.string),
            FieldSpec(name="z", type=FieldType.string),
            FieldSpec(name="w", type=FieldType.string),
        ]
    )
    reference = [
        {"n": n, "price": {"amount": float(p), "currency": "USD"}, "x": p * 2 + 1, "y": "k", "z": "k", "w": "k"}
        for n, p in [("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5), ("F", 6)]
    ]
    program = parse_program({"item": "li.d", "fields": {"n": "b", "price": "i", "x": "u", "y": {"xpath": "'k'"}}})
    check = check_program(program, schema, [Sample("https://s.example/", page)], CTX, reference=reference)
    assert not check.ok and any(e.startswith("x: 6 of 6 value(s) differ") for e in check.errors)


def test_xpath_text_and_attribute_results() -> None:
    doc = parse(
        '<html><body><ul class="breadcrumb"><li><a href="/">Home</a></li><li><a href="/c/poetry/">Poetry</a></li></ul><table><tr><th>UPC</th><td>a897fe39</td></tr></table></body></html>'
    )
    rules = parse_program(
        {
            "fields": {
                "category": {"xpath": "//ul[@class='breadcrumb']/li[2]/a/text()"},
                "link": {"xpath": "//ul[@class='breadcrumb']/li[2]/a/@href"},
                "upc": {"xpath": "//th[normalize-space()='UPC']/following-sibling::td[1]/text()"},
            }
        }
    )
    values = {k: v.value for k, v in run_detail(doc, rules).values.items()}
    assert values == {"category": "Poetry", "link": "/c/poetry/", "upc": "a897fe39"}


def test_lists_of_objects_compare_field_by_field() -> None:
    from sift.extract.learn import _values_equal

    want = [{"name": "Small", "in_stock": False}, {"name": "Large", "in_stock": True}]
    assert _values_equal([{"name": "Large", "in_stock": True}, {"name": "Small", "in_stock": False}], want)
    assert not _values_equal([{"name": "Small", "in_stock": False}, {"name": "Large", "in_stock": None}], want)
    assert not _values_equal([{"name": "Small", "in_stock": False}], want)
