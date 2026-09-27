// /changes: what each publish added, changed and closed, newest first, with
// its Atom feed. Cached and revalidated every ten minutes.

import type { Metadata } from "next";
import { LanguageLinks } from "../../_ui/chrome";
import { InstantDate, Tx } from "../../_ui/grant-bits";
import { BackendUnavailable, getCached } from "@/lib/backend";
import { formatMonth } from "@/lib/format";
import type { PublicKey } from "@/lib/public-i18n";
import type { ChangeEntry, GrantBrief, ListBody } from "@/lib/public-types";
import { loadSite, pageMetadata, type Site } from "@/lib/site";

export const revalidate = 600;

type Props = { params: Promise<{ ui: string }> };

async function loadChanges(): Promise<ChangeEntry[]> {
  // At most 100 publishes: sixteen years at one sweep every two months.
  const res = await getCached<ListBody<ChangeEntry>>("/api/v1/changes?limit=100");
  if (!res.body) throw new BackendUnavailable(`changes: ${res.status}`);
  return res.body.data;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui } = await params;
  const site = await loadSite(ui);
  return pageMetadata(site, {
    path: "/changes",
    title: site.t("changes_title"),
    description: site.t("changes_intro"),
    json: "/changes.json",
    atom: "/changes/feed.xml",
  });
}

function fieldNames(site: Site, fields: string[]): string {
  return fields.map((f) => {
    const key = `field.${f}` as PublicKey;
    const label = site.t(key);
    return label === key ? f : label;
  }).join(", ");
}

function Briefs({ site, items, fields }: { site: Site; items: (GrantBrief & { fields?: string[] })[]; fields?: boolean }) {
  if (items.length === 0) return <p>{site.t("ch_none")}</p>;
  return (
    <ul className="list-disc pl-6">
      {items.map((item) => (
        <li key={item.id}>
          <a href={site.link(`/grants/${encodeURIComponent(item.id)}`)}>{item.title || item.id}</a>
          {fields && item.fields && item.fields.length > 0 && (
            <span className="text-slate-700"> ({site.t("ch_fields", { fields: fieldNames(site, item.fields) })})</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export default async function ChangesRoute({ params }: Props) {
  const { ui } = await params;
  const [site, changes] = await Promise.all([loadSite(ui), loadChanges()]);
  return (
    <div>
      <LanguageLinks site={site} path="/changes" />
      <h1 className="text-3xl font-bold mb-2">{site.t("changes_title")}</h1>
      <p className="mb-2">{site.t("changes_intro")}</p>
      <p className="mb-6">
        <a href="/changes/feed.xml" type="application/atom+xml">{site.t("changes_feed")}</a>{" · "}
        <a href="/changes.json" type="application/json">{site.t("changes_json")}</a>
      </p>
      {changes.length === 0 && <p>{site.t("changes_empty")}</p>}
      {changes.map((entry, i) => (
        <section key={entry.published_at ?? i} aria-labelledby={`publish-${i}`} className="mb-8">
          <h2 id={`publish-${i}`} className="text-2xl font-semibold">
            <Tx site={site} k="changes_published" values={{ date: <InstantDate site={site} iso={entry.published_at} /> }} />
          </h2>
          {entry.sweep_month && formatMonth(entry.sweep_month, site.locale) && (
            <p className="text-slate-700">{site.t("changes_sweep", { month: formatMonth(entry.sweep_month, site.locale) ?? "" })}</p>
          )}
          <h3 className="font-semibold mt-3">{site.t("ch_new")}</h3>
          <Briefs site={site} items={entry.new} />
          <h3 className="font-semibold mt-3">{site.t("ch_changed")}</h3>
          <Briefs site={site} items={entry.changed} fields />
          <h3 className="font-semibold mt-3">{site.t("ch_closed")}</h3>
          <Briefs site={site} items={entry.closed} />
        </section>
      ))}
    </div>
  );
}
