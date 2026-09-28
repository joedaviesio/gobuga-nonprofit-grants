// The public landing page at /. Server-rendered from /api/v1 only: what
// GoBuga is, the live counts, the fit form, grants closing soon, links to
// browse, the subscribe form and one paragraph offering the free workspace.
// Cached and revalidated every ten minutes.

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { LanguageLinks, SubscribeForm } from "../_ui/chrome";
import { FitForm, regionOptions, tagOptions } from "../_ui/forms";
import { GrantLine, InstantDate } from "../_ui/grant-bits";
import { getCached } from "@/lib/backend";
import { formatMonth } from "@/lib/format";
import { langFromSegment } from "@/lib/public-routes";
import type { GrantRecord, ListBody, StatsBody } from "@/lib/public-types";
import { facetHref } from "@/lib/public-urls";
import { loadSite, loadTaxonomy, pageMetadata } from "@/lib/site";

export const revalidate = 600;

type Props = { params: Promise<{ ui: string }> };

const CLOSING_SOON = 6;

/** Only the language segments proxy.ts writes reach a page; anything else is a 404. */
function checkSegment(ui: string): void {
  if (ui !== "ui-auto" && langFromSegment(ui) === null) notFound();
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui } = await params;
  checkSegment(ui);
  const site = await loadSite(ui);
  return pageMetadata(site, {
    path: "/",
    title: { absolute: `GoBuga: ${site.t("home_title", { country: site.countryLabel })}` },
    description: site.t("home_lead", { country: site.countryLabel }),
    json: null,
  });
}

export default async function Home({ params }: Props) {
  const { ui } = await params;
  checkSegment(ui);
  const [site, taxonomy, stats, closing] = await Promise.all([
    loadSite(ui),
    loadTaxonomy(),
    getCached<StatsBody>("/api/v1/stats"),
    getCached<ListBody<GrantRecord>>(`/api/v1/opportunities?deadline=dated&sort=deadline&limit=${CLOSING_SOON}`),
  ]);
  const t = site.t;
  const d = stats.body?.dataset;
  const published = Boolean(d?.published_at);
  const nf = new Intl.NumberFormat(site.locale);

  return (
    <div>
      <LanguageLinks site={site} path="/" />
      <h1 className="text-3xl font-bold mb-2">{t("home_title", { country: site.countryLabel })}</h1>
      <p className="text-lg mb-6">{t("home_lead", { country: site.countryLabel })}</p>

      <section aria-labelledby="counts" className="mb-8">
        <h2 id="counts" className="text-2xl font-semibold mb-2">{t("home_counts_title")}</h2>
        {!published || !d ? <p>{t("home_nothing")}</p> : (
          <dl className="facts">
            <dt>{t("count_live")}</dt><dd><a href={site.link("/grants")}>{nf.format(d.live_count)}</a></dd>
            <dt>{t("count_closed")}</dt><dd><a href={site.link("/grants", { status: "closed" })}>{nf.format(d.closed_count)}</a></dd>
            <dt>{t("count_funders")}</dt><dd><a href={site.link("/funders")}>{nf.format(d.funder_count)}</a></dd>
            <dt>{t("count_last_sweep")}</dt><dd>{formatMonth(d.last_sweep, site.locale) ?? t("not_stated")}</dd>
            <dt>{t("count_published")}</dt><dd><InstantDate site={site} iso={d.published_at} /></dd>
          </dl>
        )}
      </section>

      <section aria-labelledby="fit" className="mb-8">
        <h2 id="fit" className="text-2xl font-semibold mb-2">{t("home_fit_title")}</h2>
        <p className="mb-3">{t("fit_intro")}</p>
        <FitForm site={site} taxonomy={taxonomy} values={{}} idPrefix="home-fit" />
      </section>

      {published && (
        <section aria-labelledby="closing" className="mb-8">
          <h2 id="closing" className="text-2xl font-semibold">{t("home_closing_title")}</h2>
          {!closing.body || closing.body.data.length === 0 ? <p>{t("home_closing_none")}</p> : (
            <ul>{closing.body.data.map((g) => <GrantLine key={g.id} site={site} grant={g} />)}</ul>
          )}
          <p className="mt-2"><a href={site.link("/grants")}>{t("home_all_grants")}</a></p>
        </section>
      )}

      <section aria-labelledby="browse" className="mb-8">
        <h2 id="browse" className="text-2xl font-semibold mb-2">{t("home_browse_title")}</h2>
        <h3 className="font-semibold">{t("home_by_tag")}</h3>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 mb-3">
          {tagOptions(taxonomy).map(([slug, label]) => (
            <li key={slug}><a href={facetHref("tag", slug, site.langs.carry)}>{label}</a></li>
          ))}
        </ul>
        <h3 className="font-semibold">{t("home_by_region")}</h3>
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {regionOptions(taxonomy).map(([slug, label]) => (
            <li key={slug}><a href={facetHref("region", slug, site.langs.carry)}>{label}</a></li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="subscribe" className="mb-8">
        <h2 id="subscribe" className="text-2xl font-semibold mb-2">{t("subscribe_title")}</h2>
        <p className="mb-1">{t("subscribe_what")}</p>
        <p className="mb-3">{t("subscribe_how")}</p>
        <SubscribeForm site={site} id="home-email" />
      </section>

      <section aria-labelledby="workspace">
        <h2 id="workspace" className="text-2xl font-semibold mb-2">{t("home_workspace_title")}</h2>
        <p>
          {t("home_workspace_body")}{" "}
          <a href="/register">{t("home_register")}</a>{" · "}
          <a href="/login">{t("home_sign_in")}</a>
        </p>
      </section>
    </div>
  );
}
