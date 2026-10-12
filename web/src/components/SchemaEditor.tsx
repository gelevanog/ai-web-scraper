"use client";

import type { FieldSpec, FieldType, Rejected, Schema } from "@/lib/types";

const TYPES: FieldType[] = ["string", "integer", "number", "boolean", "money", "date", "url", "enum", "list", "object"];

function typeDetail(field: FieldSpec): string {
  if (field.type === "money") return field.currency ?? "USD";
  if (field.type === "enum") return (field.enum ?? []).join(" | ");
  if (field.type === "number" && field.unit) return `in ${field.unit}`;
  if (field.type === "list" && field.items) {
    if (field.items.type === "object") return `of {${(field.items.fields ?? []).map((f) => f.name).join(", ")}}`;
    return `of ${field.items.type}${field.items.enum ? ` (${field.items.enum.join(" | ")})` : ""}`;
  }
  return "";
}

export function SchemaEditor({ schema, rejected, onChange }: { schema: Schema; rejected: Rejected[]; onChange: (schema: Schema) => void }) {
  const update = (index: number, patch: Partial<FieldSpec>) => {
    const fields = schema.fields.map((f, i) => (i === index ? { ...f, ...patch } : f));
    onChange({ ...schema, fields });
  };
  const remove = (index: number) => onChange({ ...schema, fields: schema.fields.filter((_, i) => i !== index) });
  return (
    <div>
      <table className="w-full text-left text-[13px]">
        <thead>
          <tr className="text-[11px] tracking-wide text-muted uppercase">
            <th className="pb-1.5 font-medium">field</th>
            <th className="pb-1.5 font-medium">type</th>
            <th className="pb-1.5 font-medium">detail</th>
            <th className="pb-1.5 text-center font-medium">req.</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {schema.fields.map((field, index) => (
            <tr key={index} className="border-t border-line/70">
              <td className="py-1 pr-2">
                <input
                  className="mono w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-[12.5px] text-ink hover:border-line focus:border-iris focus:outline-none"
                  value={field.name}
                  onChange={(e) => update(index, { name: e.target.value })}
                />
                {field.contact === "business" ? <div className="px-1 text-[10px] text-teal">business contact: kept by the PII guard</div> : null}
              </td>
              <td className="py-1 pr-2">
                <select className="rounded border border-line bg-white px-1 py-0.5 text-[12.5px]" value={field.type} onChange={(e) => update(index, { type: e.target.value as FieldType })}>
                  {TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </td>
              <td className="mono py-1 pr-2 text-[11.5px] text-ink-soft">{typeDetail(field)}</td>
              <td className="py-1 text-center">
                <input type="checkbox" checked={Boolean(field.required)} onChange={(e) => update(index, { required: e.target.checked })} />
              </td>
              <td className="py-1 text-right">
                <button className="px-1 text-muted hover:text-rose" onClick={() => remove(index)} aria-label={`remove ${field.name}`}>
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rejected.length ? (
        <div className="mt-3 rounded-lg bg-amber-soft px-3 py-2 text-xs text-amber">
          <div className="font-semibold">Not added to the schema</div>
          {rejected.map((r, i) => (
            <div key={i}>
              <span className="mono">{r.name ?? "?"}</span>: {r.reason}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
