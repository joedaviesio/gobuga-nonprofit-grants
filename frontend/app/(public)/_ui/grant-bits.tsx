// Small server components shared by the public pages: dates, amounts,
// deadlines and one line per grant in a list. React escapes every string it
// renders, so untrusted titles and excerpts from funder sites stay text.

import { Fragment, type ReactNode } from "react";
import {
  amountText, formatCalendarDate, formatInstantDate, isoDateIn, type Amount,
} from "@/lib/format";
import type { PublicKey } from "@/lib/public-i18n";
import type { GrantRecord } from "@/lib/public-types";
import type { Site } from "@/lib/site";

/** Translated text with React nodes in place of its `{name}` placeholders. */
export function Tx({ site, k, values }: { site: Site; k: PublicKey; values: Record<string, ReactNode> }) {
  const template = site.t(k);
  const parts = template.split(/(\{\w+\})/);
  return (
    <>
      {parts.map((part, i) => {
        const name = /^\{(\w+)\}$/.exec(part)?.[1];
        return <Fragment key={i}>{name && name in values ? values[name] : part}</Fragment>;
      })}
    </>
  );
}

/** A calendar date (`2026-11-30`) in a <time> element. */
export function CalendarDate({ site, date }: { site: Site; date: string | null }) {
  const text = formatCalendarDate(date, site.locale);
  return text && date ? <time dateTime={date}>{text}</time> : null;
}

/** The local date an ISO timestamp fell on, in a <time> element. */
export function InstantDate({ site, iso }: { site: Site; iso: string | null }) {
  const text = formatInstantDate(iso, site.locale, site.timezone);
  const day = isoDateIn(iso, site.timezone);
  return text && day ? <time dateTime={day}>{text}</time> : null;
}

export function amountString(site: Site, amount: Amount | null | undefined): string {
  const a = amountText(amount, site.locale);
  switch (a.kind) {
    case "range": return site.t("amount_range", { min: a.min, max: a.max });
    case "up_to": return site.t("amount_up_to", { max: a.max });
    case "from": return site.t("amount_from", { min: a.min });
    case "exact": return site.t("amount_exact", { min: a.min });
    default: return site.t("amount_none");
  }
}

/** The deadline as plain text, for titles and meta descriptions; same wording as <Deadline>. */
export function deadlineString(site: Site, grant: GrantRecord): string {
  const { state, date } = grant.deadline;
  const calendar = formatCalendarDate(date, site.locale);
  if (grant.status === "closed") {
    if (state === "dated" && calendar) return site.t("closed_on", { date: calendar });
    const closed = formatInstantDate(grant.closed_at, site.locale, site.timezone);
    return closed ? site.t("closed_on", { date: closed }) : site.t("deadline_closed");
  }
  if (state === "dated" && calendar) return site.t("deadline_dated", { date: calendar });
  if (state === "rolling-confirmed") return site.t("deadline_rolling");
  return site.t("deadline_none");
}

/** The deadline as a person reads it: "Closes 30 November 2026", "Rolling", "Closed 20 August 2026". */
export function Deadline({ site, grant }: { site: Site; grant: GrantRecord }) {
  const { state, date } = grant.deadline;
  if (grant.status === "closed") {
    if (state === "dated" && date) {
      return <Tx site={site} k="closed_on" values={{ date: <CalendarDate site={site} date={date} /> }} />;
    }
    if (grant.closed_at) {
      return <Tx site={site} k="closed_on" values={{ date: <InstantDate site={site} iso={grant.closed_at} /> }} />;
    }
    return <>{site.t("deadline_closed")}</>;
  }
  if (state === "dated" && date && formatCalendarDate(date, site.locale)) {
    return <Tx site={site} k="deadline_dated" values={{ date: <CalendarDate site={site} date={date} /> }} />;
  }
  if (state === "rolling-confirmed") return <>{site.t("deadline_rolling")}</>;
  return <>{site.t("deadline_none")}</>;
}

function verifiedIso(site: Site, grant: GrantRecord): string | null {
  const iso = grant.deadline.verified_at || grant.verified_at;
  return formatInstantDate(iso, site.locale, site.timezone) ? iso : null;
}

/** "Deadline verified 4 October 2026", or nothing if the record has no date. */
export function Verified({ site, grant }: { site: Site; grant: GrantRecord }) {
  const iso = verifiedIso(site, grant);
  if (!iso) return null;
  return <Tx site={site} k="verified_line" values={{ date: <InstantDate site={site} iso={iso} /> }} />;
}

export function statusLabel(site: Site, status: string): string {
  if (status === "closed") return site.t("status_closed");
  if (status === "stale") return site.t("status_stale");
  return site.t("status_live");
}

/** One grant in a list: its title as a link, then funder, amount, deadline and verification. */
export function GrantLine({ site, grant, hrefFor, extra }: {
  site: Site;
  grant: GrantRecord;
  hrefFor?: (id: string) => string;
  extra?: ReactNode;
}) {
  const link = hrefFor ? hrefFor(grant.id) : site.link(`/grants/${encodeURIComponent(grant.id)}`);
  return (
    <li className="py-3 border-b border-slate-200">
      <h3 className="text-lg font-semibold leading-snug">
        <a href={link}>{grant.title || grant.id}</a>
      </h3>
      <p className="text-slate-700">
        {grant.funder.name}
        {" · "}
        {amountString(site, grant.amount)}
        {" · "}
        <Deadline site={site} grant={grant} />
        {grant.status !== "closed" && verifiedIso(site, grant) && (
          <>
            {" · "}
            <Verified site={site} grant={grant} />
          </>
        )}
      </p>
      {extra}
    </li>
  );
}
