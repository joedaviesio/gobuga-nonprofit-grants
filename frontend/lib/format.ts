// Dates and money for the public pages, formatted on the server in the
// country's locale and timezone, never from the visitor's clock, so every
// reader (and every cache) gets the same HTML.
//
// Pure and dependency-free so it can be tested with `node --test`.

/** BCP 47 locale for a UI language in a country: `en-NZ`, `ro-MD`, `ru-MD`. */
export function localeFor(lang: string, country: string): string {
  const tag = `${lang}-${country.toUpperCase()}`;
  try {
    return Intl.getCanonicalLocales(tag)[0];
  } catch {
    return lang;
  }
}

function safeTimeZone(tz: string): string {
  try {
    new Intl.DateTimeFormat("en", { timeZone: tz });
    return tz;
  } catch {
    return "UTC";
  }
}

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * A calendar date (`2026-11-30`) as `30 November 2026` in the locale. The
 * date has no time of day, so no timezone shifts it. Null if malformed.
 */
export function formatCalendarDate(value: string | null | undefined, locale: string): string | null {
  const m = value ? DATE_RE.exec(value) : null;
  if (!m) return null;
  const when = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(when.getTime()) || when.getUTCDate() !== Number(m[3])) return null;
  return new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(when);
}

function parseInstant(value: string | null | undefined): Date | null {
  if (!value) return null;
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? null : when;
}

/** An ISO timestamp as the local date it fell on in `tz`: `4 October 2026`. */
export function formatInstantDate(value: string | null | undefined, locale: string, tz: string): string | null {
  const when = parseInstant(value);
  if (!when) return null;
  return new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone: safeTimeZone(tz) })
    .format(when);
}

/** The `YYYY-MM-DD` a timestamp fell on in `tz`, for `<time datetime>`. */
export function isoDateIn(value: string | null | undefined, tz: string): string | null {
  const when = parseInstant(value);
  if (!when) return null;
  // en-CA formats dates as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: safeTimeZone(tz) })
    .format(when);
}

/** `2026-09` as `September 2026`. Null if malformed. */
export function formatMonth(value: string | null | undefined, locale: string): string | null {
  const m = value ? /^(\d{4})-(\d{2})$/.exec(value) : null;
  if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return null;
  const when = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1));
  return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone: "UTC" }).format(when);
}

/** The `count` months before `month` (YYYY-MM), newest first: `2026-01` -> `2025-12`, ... */
export function previousMonths(month: string, count = 12): string[] {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  if (!m) return [];
  const start = Number(m[1]) * 12 + Number(m[2]) - 1;
  const out: string[] = [];
  for (let i = 1; i <= count; i++) {
    const total = start - i;
    out.push(`${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`);
  }
  return out;
}

/** A whole or fractional amount in the currency, e.g. `$20,000` in en-NZ. */
export function formatMoney(value: number, currency: string | null | undefined, locale: string): string {
  const digits = Number.isInteger(value) ? 0 : 2;
  if (currency) {
    try {
      return new Intl.NumberFormat(locale, {
        style: "currency", currency, minimumFractionDigits: digits, maximumFractionDigits: digits,
      }).format(value);
    } catch {
      // Not an ISO 4217 code: fall through and print the code after the number.
    }
  }
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value);
  return currency ? `${number} ${currency}` : number;
}

export interface Amount {
  min: number | null;
  max: number | null;
  currency: string | null;
}

export type AmountText =
  | { kind: "none" }
  | { kind: "exact"; min: string }
  | { kind: "range"; min: string; max: string }
  | { kind: "up_to"; max: string }
  | { kind: "from"; min: string };

function positive(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * What an amount states, formatted; the page words it ("Up to $20,000").
 * Zero or missing bounds are "not stated", matching the API's amount_range.
 */
export function amountText(amount: Amount | null | undefined, locale: string): AmountText {
  const min = positive(amount?.min);
  const max = positive(amount?.max);
  const fmt = (v: number) => formatMoney(v, amount?.currency, locale);
  if (min !== null && max !== null) {
    if (min === max) return { kind: "exact", min: fmt(min) };
    if (min > max) return { kind: "none" };
    return { kind: "range", min: fmt(min), max: fmt(max) };
  }
  if (max !== null) return { kind: "up_to", max: fmt(max) };
  if (min !== null) return { kind: "from", min: fmt(min) };
  return { kind: "none" };
}
