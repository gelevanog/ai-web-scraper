"""The typed schema a job extracts: fields with types that code can validate and normalize.

A schema is proposed by the model from the user's plain-English request ("every product with name, price in USD,
rating..."), shown to the user, edited if needed, and from then on owned by code: the model never changes it during
extraction, and no page text can add a field. It converts to JSON Schema (for the API, the UI and the prompts) and
to a Pydantic model (for validation)."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class FieldType(StrEnum):
    string = "string"
    integer = "integer"
    number = "number"
    boolean = "boolean"
    money = "money"
    """An amount with a currency, normalized to the field's target currency with the configured FX table."""
    date = "date"
    """ISO date; relative dates ("3 days ago", "tomorrow") are resolved against the crawl date."""
    datetime = "datetime"
    url = "url"
    """Absolute URL, resolved against the page URL."""
    enum = "enum"
    list = "list"
    object = "object"


SCALAR_TYPES = frozenset(
    {
        FieldType.string,
        FieldType.integer,
        FieldType.number,
        FieldType.boolean,
        FieldType.money,
        FieldType.date,
        FieldType.datetime,
        FieldType.url,
        FieldType.enum,
    }
)


class FieldSpec(BaseModel):
    name: str
    type: FieldType
    description: str = ""
    required: bool = False
    enum: list[str] | None = None
    """Allowed values for enum fields (snake_case)."""
    items: FieldSpec | None = None
    """Element type of a list field."""
    fields: list[FieldSpec] | None = None
    """Children of an object field."""
    currency: str | None = None
    """Target currency of a money field (ISO 4217), default USD."""
    unit: str | None = None
    """Target unit of a number field, e.g. "sqm", "kg", "km" (converted from what the page shows)."""
    contact: Literal["none", "business"] = "none"
    """"business": the user explicitly asked for business contact details (a shop's phone, a listing agent's office
    line). Without it the PII guard drops email addresses and phone numbers from this field."""
    key: bool = False
    """Part of the record's identity (cross-page dedupe, matching records across re-crawls)."""

    @field_validator("name")
    @classmethod
    def _snake(cls, value: str) -> str:
        value = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
        if not _NAME.match(value):
            raise ValueError(f"invalid field name {value!r}")
        return value

    @model_validator(mode="after")
    def _shape(self) -> FieldSpec:
        if self.type == FieldType.enum:
            if not self.enum:
                raise ValueError(f"enum field {self.name!r} needs values")
            self.enum = [re.sub(r"[^a-z0-9_]+", "_", v.strip().lower()).strip("_") for v in self.enum]
        if self.type == FieldType.list and self.items is None:
            self.items = FieldSpec(name="item", type=FieldType.string)
        if self.type == FieldType.object and not self.fields:
            raise ValueError(f"object field {self.name!r} needs child fields")
        if self.type == FieldType.money and not self.currency:
            self.currency = "USD"
        return self

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any]
        if self.type == FieldType.string:
            schema = {"type": "string"}
        elif self.type == FieldType.integer:
            schema = {"type": "integer"}
        elif self.type == FieldType.number:
            schema = {"type": "number"}
            if self.unit:
                schema["x-unit"] = self.unit
        elif self.type == FieldType.boolean:
            schema = {"type": "boolean"}
        elif self.type == FieldType.money:
            schema = {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "currency": {"type": "string", "const": self.currency},
                    "source_amount": {"type": "number"},
                    "source_currency": {"type": "string"},
                },
                "required": ["amount", "currency"],
                "x-sift-type": "money",
            }
        elif self.type == FieldType.date:
            schema = {"type": "string", "format": "date"}
        elif self.type == FieldType.datetime:
            schema = {"type": "string", "format": "date-time"}
        elif self.type == FieldType.url:
            schema = {"type": "string", "format": "uri"}
        elif self.type == FieldType.enum:
            schema = {"type": "string", "enum": list(self.enum or [])}
        elif self.type == FieldType.list:
            assert self.items is not None
            schema = {"type": "array", "items": self.items.json_schema()}
        else:
            children = self.fields or []
            schema = {
                "type": "object",
                "properties": {child.name: child.json_schema() for child in children},
                "required": [child.name for child in children if child.required],
            }
        if self.description:
            schema["description"] = self.description
        if self.contact == "business":
            schema["x-contact"] = "business"
        return schema

    def short(self) -> str:
        """One line for prompts: `price: money (USD), required - the current price`."""
        kind = self.type.value
        if self.type == FieldType.money:
            kind += f" ({self.currency})"
        elif self.type == FieldType.enum:
            kind += f" [{', '.join(self.enum or [])}]"
        elif self.type == FieldType.list and self.items is not None:
            if self.items.type == FieldType.object:
                kind = "list of objects {" + ", ".join(child.short() for child in self.items.fields or []) + "}"
            else:
                kind = f"list of {self.items.type.value}"
                if self.items.type == FieldType.enum:
                    kind += f" [{', '.join(self.items.enum or [])}]"
        elif self.type == FieldType.number and self.unit:
            kind += f" (in {self.unit})"
        elif self.type == FieldType.object:
            kind = "object {" + ", ".join(child.short() for child in self.fields or []) + "}"
        flags = [f for f, on in (("required", self.required), ("business contact", self.contact == "business")) if on]
        text = f"{self.name}: {kind}"
        if flags:
            text += ", " + ", ".join(flags)
        if self.description:
            text += f" - {self.description}"
        return text


class Schema(BaseModel):
    name: str = "records"
    description: str = ""
    fields: list[FieldSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> Schema:
        names = [f.name for f in self.fields]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate field names: {sorted(duplicates)}")
        if not self.fields:
            raise ValueError("a schema needs at least one field")
        return self

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def key_fields(self) -> list[str]:
        keys = [f.name for f in self.fields if f.key]
        if keys:
            return keys
        # Default identity: the required strings and URLs (name, title, url...), else the first field.
        guess = [f.name for f in self.fields if f.required and f.type in {FieldType.string, FieldType.url}]
        return guess[:2] or [self.fields[0].name]

    def json_schema(self) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": self.name,
            "description": self.description,
            "type": "object",
            "properties": {f.name: f.json_schema() for f in self.fields},
            "required": [f.name for f in self.fields if f.required],
            "additionalProperties": False,
        }

    def prompt_lines(self) -> str:
        return "\n".join(f"- {f.short()}" for f in self.fields)
