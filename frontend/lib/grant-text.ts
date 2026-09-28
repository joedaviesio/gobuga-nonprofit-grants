// A published grant row worded the way the public pages word it, for the
// workspace's grant cards. Same formatters (lib/format.ts) and same text
// (lib/public-i18n.ts) as app/(public)/_ui/grant-bits.tsx, so a grant reads
// the same on both sides: "Up to $30,000 · Closes 5 October 2026 · Deadline
// verified 25 September 2026".
//
// Pure and dependency-free so it can be tested with `node --test`.

import { amountText, formatCalendarDate, formatInstantDate } from "@/lib/format";
import { t, type PublicKey } from "@/lib/public-i18n";

/** The fields of a published row these helpers read. */
export interface GrantTextRow {
  deadline?: string | null;
  deadline_state?: string;
  amount_min?: number | null;
  amount_max?: number | null;
  currency?: string | null;
  verified_at?: string;
  provenance?: { deadline?: { verified_at?: string } };
}

/** "Up to $30,000", "$2,000 to $40,000", "Not stated" (public amountString). */
export function amountLabel(row: GrantTextRow, lang: string, locale: string): string {
  const a = amountText({ min: row.amount_min ?? null, max: row.amount_max ?? null, currency: row.currency ?? null }, locale);
  switch (a.kind) {
    case "range": return t(lang, "amount_range", { min: a.min, max: a.max });
    case "up_to": return t(lang, "amount_up_to", { max: a.max });
    case "from": return t(lang, "amount_from", { min: a.min });
    case "exact": return t(lang, "amount_exact", { min: a.min });
    default: return t(lang, "amount_none");
  }
}

/** "Closes 5 October 2026", "Rolling: …", or no closing date (public deadlineString, live rows). */
export function deadlineLabel(row: GrantTextRow, lang: string, locale: string): string {
  const calendar = formatCalendarDate(row.deadline, locale);
  if (row.deadline_state === "dated" && calendar) return t(lang, "deadline_dated", { date: calendar });
  if (row.deadline_state === "rolling-confirmed") return t(lang, "deadline_rolling");
  return t(lang, "deadline_none");
}

/** "Deadline verified 25 September 2026", "Checked …", or null (public <Verified>). */
export function verifiedLabel(row: GrantTextRow, lang: string, locale: string, tz: string): string | null {
  const iso = row.provenance?.deadline?.verified_at || row.verified_at;
  const date = formatInstantDate(iso, locale, tz);
  if (!date) return null;
  const dated = row.deadline_state === "dated" || row.deadline_state === "rolling-confirmed";
  const key: PublicKey = dated ? "verified_line" : "prov_checked";
  return t(lang, key, { date });
}
