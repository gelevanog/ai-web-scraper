from __future__ import annotations

import datetime as dt

import pytest

from sift.normalize.dates import day_first_for_locale, parse_date
from sift.normalize.money import FxTable, parse_money, parse_money_range
from sift.normalize.text import clean_string, match_enum, normalize_phone, parse_bool
from sift.normalize.units import parse_integer, parse_number, parse_quantity
from sift.schema.model import FieldSpec, FieldType
from sift.validate.normalize import Context, normalize_value

TODAY = dt.date(2026, 10, 1)  # a Thursday
FX = FxTable({"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CHF": 1.13, "JPY": 0.0067, "CAD": 0.73})


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("$1,299.00", 1299.0, "USD"),
        ("1.299,00 €", 1299.0, "EUR"),
        ("1 299,00 €", 1299.0, "EUR"),
        ("€1,299", 1299.0, "EUR"),
        ("EUR 1.299,50", 1299.5, "EUR"),
        ("CHF 1'299.00", 1299.0, "CHF"),
        ("1'299.00 CHF", 1299.0, "CHF"),
        ("¥12,800", 12800.0, "JPY"),
        ("12,800 円", 12800.0, "JPY"),
        ("C$49.99", 49.99, "CAD"),
        ("CA$49.99", 49.99, "CAD"),
        ("£12.50", 12.5, "GBP"),
        ("12.50 USD", 12.5, "USD"),
        ("$85k", 85000.0, "USD"),
        ("Was $1,499.00 Now $1,299.00", 1299.0, "USD"),
        ("Free", 0.0, None),
        ("Tickets $25", 25.0, "USD"),
    ],
)
def test_parse_money(text: str, amount: float, currency: str | None) -> None:
    money = parse_money(text)
    assert money is not None and money.amount == amount and money.currency == currency


def test_money_without_a_price() -> None:
    assert parse_money("Custom pricing - contact sales") is None
    assert parse_money("") is None
    assert parse_money("No reviews yet") is None


@pytest.mark.parametrize(
    ("text", "lo", "hi"),
    [
        ("$85,000 – $110,000 a year", 85000, 110000),
        ("$85k–$110k", 85000, 110000),
        ("$85–110k", 85000, 110000),
        ("60.000 € – 75.000 € per year", 60000, 75000),
        ("Up to £60,000", None, 60000),
        ("From €40.000", 40000, None),
    ],
)
def test_money_ranges(text: str, lo: float | None, hi: float | None) -> None:
    low, high = parse_money_range(text)
    assert (low.amount if low else None) == lo
    assert (high.amount if high else None) == hi


def test_fx_conversion() -> None:
    assert FX.convert(100, "EUR", "USD") == 108.0
    assert FX.convert(108, "USD", "EUR") == 100.0
    assert FX.convert(10, "XYZ", "USD") is None


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Posted 3 days ago", "2026-09-28"),
        ("3d ago", "2026-09-28"),
        ("2w ago", "2026-09-17"),
        ("Posted today", "2026-10-01"),
        ("Posted yesterday", "2026-09-30"),
        ("Tomorrow, 20:00", "2026-10-02"),
        ("in 5 days", "2026-10-06"),
        ("This Saturday", "2026-10-03"),
        ("next Thursday", "2026-10-08"),
        ("Wed, Oct 14", "2026-10-14"),
        ("Fri 2 Jan", "2027-01-02"),
        ("October 14, 2026", "2026-10-14"),
        ("14 October 2026", "2026-10-14"),
        ("14.10.2026", "2026-10-14"),
        ("2026-10-14", "2026-10-14"),
        ("an hour ago", "2026-10-01"),
    ],
)
def test_parse_date(text: str, want: str) -> None:
    parsed = parse_date(text, TODAY)
    assert parsed is not None and parsed.isoformat() == want


def test_ambiguous_numeric_dates_follow_the_locale() -> None:
    assert parse_date("03/04/2026", TODAY, day_first=False) == dt.date(2026, 3, 4)
    assert parse_date("03/04/2026", TODAY, day_first=True) == dt.date(2026, 4, 3)
    assert parse_date("25/04/2026", TODAY, day_first=False) == dt.date(2026, 4, 25)  # 25 cannot be a month
    assert (
        day_first_for_locale("en-US") is False
        and day_first_for_locale("en-GB") is True
        and day_first_for_locale(None) is None
    )
    assert parse_date("no date here", TODAY) is None


@pytest.mark.parametrize(
    ("text", "unit", "want"),
    [
        ("1,200 sq ft", "sqm", 111.48),
        ("1200 ft²", "sqm", 111.48),
        ("85 m²", "sqm", 85.0),
        ("85 sqm", "sqm", 85.0),
        ("5 lb", "kg", 2.27),
        ("500 g", "kg", 0.5),
        ("3 mi", "km", 4.83),
        ("42", "sqm", 42.0),
    ],
)
def test_units(text: str, unit: str, want: float) -> None:
    assert parse_quantity(text, unit) == want


def test_numbers() -> None:
    assert parse_number("4.5 out of 5") == 4.5
    assert parse_number("Rated 4/5") == 4.0
    assert parse_number("★★★½☆") == 3.5
    assert parse_number("★★★★☆") == 4.0
    assert parse_number("Three") == 3.0
    assert parse_number("1.234,5") == 1234.5
    assert parse_integer("Up to 20 users") == 20
    assert parse_integer("Unlimited users") is None
    assert parse_number("n/a") is None


def test_text_bool_enum_phone() -> None:
    assert clean_string("  Northside Library¹ ") == "Northside Library"
    assert parse_bool("100% remote") is True and parse_bool("Hybrid") is False and parse_bool("On-site") is False
    assert parse_bool("Sold out") is False and parse_bool(True) is True and parse_bool("maybe") is None
    stock = ["in_stock", "out_of_stock", "preorder"]
    assert match_enum("In stock", stock) == ("in_stock", 1.0)
    assert match_enum("Ready to ship", stock) == ("in_stock", 0.9)
    assert match_enum("Currently unavailable", stock)[0] == "out_of_stock"
    assert match_enum("Pre-order (ships in November)", stock)[0] == "preorder"
    assert match_enum("out_of_stock", stock) == ("out_of_stock", 1.0)
    assert match_enum("Freelance / contract", ["full_time", "part_time", "contract", "internship"])[0] == "contract"
    assert match_enum("Swimming Pool", ["library", "pool", "sports_hall"])[0] == "pool"
    assert match_enum("banana", stock) == (None, 0.0)
    assert normalize_phone("+1 (503) 555-0142") == "+15035550142"
    assert normalize_phone("tel:+44 1632 961234") == "+441632961234"
    assert normalize_phone("12") is None


def test_normalize_value_by_type() -> None:
    ctx = Context(page_url="https://s.example/catalog/", today=TODAY, fx=FX, day_first=True)
    price = normalize_value("1.299,00 €", FieldSpec(name="price", type=FieldType.money), ctx)
    assert price.value == {"amount": 1402.92, "currency": "USD", "source_amount": 1299.0, "source_currency": "EUR"}
    assumed = normalize_value("1299", FieldSpec(name="price", type=FieldType.money), ctx)
    assert assumed.value == {"amount": 1299.0, "currency": "USD"} and assumed.confidence < 1
    url = normalize_value("/p/x/", FieldSpec(name="product_url", type=FieldType.url), ctx)
    assert url.value == "https://s.example/p/x/"
    assert (
        normalize_value("data:image/png;base64,xx", FieldSpec(name="image_url", type=FieldType.url), ctx).value is None
    )
    tags = FieldSpec(
        name="dietary",
        type=FieldType.list,
        items=FieldSpec(name="t", type=FieldType.enum, enum=["vegan", "vegetarian", "gluten_free"]),
    )
    assert normalize_value(["Vegan", "Gluten free", "Vegan"], tags, ctx).value == ["vegan", "gluten_free"]
    variants = FieldSpec(
        name="variants",
        type=FieldType.list,
        items=FieldSpec(
            name="v",
            type=FieldType.object,
            fields=[FieldSpec(name="name", type=FieldType.string), FieldSpec(name="in_stock", type=FieldType.boolean)],
        ),
    )
    assert normalize_value(
        [{"name": "S", "in_stock": True}, {"name": "M", "in_stock": "sold out"}], variants, ctx
    ).value == [{"name": "S", "in_stock": True}, {"name": "M", "in_stock": False}]
    relative = normalize_value("Posted 3 days ago", FieldSpec(name="posted", type=FieldType.date), ctx)
    assert relative.value == "2026-09-28" and relative.notes
    phone = normalize_value("Call +44 1632 961234", FieldSpec(name="phone", type=FieldType.string), ctx)
    assert phone.value == "+441632961234"
