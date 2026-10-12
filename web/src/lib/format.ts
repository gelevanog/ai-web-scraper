export function label(value: string): string {
  return value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    return `${u.host}${u.pathname}${u.search}`;
  } catch {
    return url;
  }
}

export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    return value.map((v) => (typeof v === "object" && v !== null ? Object.values(v as Record<string, unknown>).map(display).join(" ") : display(v))).join(", ");
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if ("amount" in obj && "currency" in obj) {
      const amount = Number(obj.amount);
      return `${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${String(obj.currency)}`;
    }
    return JSON.stringify(obj);
  }
  return String(value);
}

export function ruleText(rule: { css?: string | null; xpath?: string | null; attr?: string; regex?: string | null; all?: boolean } | null | undefined): string {
  if (!rule) return "—";
  const sel = rule.xpath ? rule.xpath : rule.css || ":self";
  const attr = rule.attr && rule.attr !== "text" ? ` @${rule.attr}` : "";
  const re = rule.regex ? ` /${rule.regex}/` : "";
  return `${sel}${attr}${re}${rule.all ? " [all]" : ""}`;
}
