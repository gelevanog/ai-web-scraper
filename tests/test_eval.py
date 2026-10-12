from __future__ import annotations

from sift.eval.runner import compare_schema
from sift.eval.score import aggregate, field_equal, score_site
from sift.eval.summary import cost_table
from sift.schema.model import FieldSpec, FieldType, Schema

SCHEMA = Schema(
    name="product",
    fields=[
        FieldSpec(name="name", type=FieldType.string, required=True, key=True),
        FieldSpec(name="price", type=FieldType.money),
        FieldSpec(name="tags", type=FieldType.list, items=FieldSpec(name="t", type=FieldType.string)),
        FieldSpec(name="url", type=FieldType.url),
    ],
)


def test_field_equality_rules() -> None:
    price, tags, url = SCHEMA.fields[1], SCHEMA.fields[2], SCHEMA.fields[3]
    assert field_equal(price, {"amount": 10.004, "currency": "USD"}, 10.0)
    assert not field_equal(price, {"amount": 10.5}, 10.0)
    assert field_equal(tags, ["B", "a"], ["a", "b"])
    assert field_equal(url, "https://S.example/x/index.html", "https://s.example/x/")
    assert field_equal(SCHEMA.fields[0], "Atlas  Headlamp!", "atlas headlamp")
    assert (
        field_equal(price, None, None)
        and not field_equal(price, None, 3.0)
        and not field_equal(price, {"amount": 3}, None)
    )


def test_score_site_precision_recall_and_errors() -> None:
    truth = [
        {"name": "A", "price": 1.0, "tags": [], "url": None},
        {"name": "B", "price": 2.0, "tags": ["x"], "url": None},
    ]
    got = [{"name": "a", "price": {"amount": 1.0}, "tags": [], "url": None}, {"name": "C", "price": {"amount": 9.0}}]
    score = score_site("s", SCHEMA, truth, got)
    assert (score.precision, score.recall) == (0.5, 0.5)
    assert score.field_accuracy == 1.0 and score.missed == ["B"] and score.unmatched_extracted == ["C"]
    total = aggregate([score, score])
    assert total["truth_records"] == 4 and total["recall"] == 0.5


def test_schema_comparison_matches_synonyms_and_types() -> None:
    proposed = Schema(
        name="p",
        fields=[
            FieldSpec(name="product_name", type=FieldType.string),
            FieldSpec(name="price_usd", type=FieldType.money, currency="USD"),
            FieldSpec(name="tags", type=FieldType.string),
            FieldSpec(name="seller", type=FieldType.string),
        ],
    )
    result = compare_schema(SCHEMA, proposed)
    assert result["found"] == 3 and result["right_type"] == 2 and result["extra"] == ["seller"]


def test_cost_table() -> None:
    table = cost_table(2, 10_000, 4_000, 100, {"m": (1.0, 2.0)})
    assert table["calls_per_1k_pages"] == 20.0
    assert table["usd_per_1k_pages"]["m"] == round((10_000 * 1 + 4_000 * 2) / 1e6 * 10, 4)
