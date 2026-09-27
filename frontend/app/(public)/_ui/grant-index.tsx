// The grant index, shared by /grants (cached whole) and its filtered and
// paginated views (rendered on request): the filter form, the results, plain
// pagination links, facet links that let a crawler reach every grant, and a
// Dataset description of the pool.

import type { Metadata } from "next";
import { JsonLd, LanguageLinks } from "./chrome";
import { amountOptions, deadlineOptions, GrantFilters, invalidMessage, regionOptions, tagOptions } from "./forms";
import { GrantLine, InstantDate, Tx } from "./grant-bits";
import { BackendUnavailable, getCached, type BackendResult } from "@/lib/backend";
import { datasetJsonLd } from "@/lib/jsonld";
import type { FunderSummary, GrantRecord, ListBody, Taxonomy } from "@/lib/public-types";
import { facetHref, href, paginate, queryString } from "@/lib/public-urls";
import { pageMetadata, type Site } from "@/lib/site";

export async function loadFunders(): Promise<FunderSummary[]> {
  const res = await getCached<ListBody<FunderSummary>>("/api/v1/funders");
  if (!res.body) throw new BackendUnavailable("funders missing");
  return res.body.data;
}

/** True when the values filter the index, not merely page through it. */
function isFiltered(values: Record<string, string>): boolean {
  return Object.keys(values).some((k) => k !== "offset" && k !== "limit");
}

export function indexMetadata(site: Site, values: Record<string, string>): Metadata {
  const query = queryString(values);
  return pageMetadata(site, {
    path: "/grants",
    title: site.t(isFiltered(values) ? "grants_title_filtered" : "grants_title", { country: site.countryLabel }),
    description: site.t("dataset_body", { country: site.countryLabel }),
    json: query ? `/grants.json?${query}` : "/grants.json",
    // A filtered or paginated view is its own page, in canonical parameter order.
    canonical: query ? site.base + href("/grants", values, site.langs.carry) : null,
  });
}

function datasetLd(site: Site, publishedAt: string | null) {
  return datasetJsonLd({
    name: site.t("grants_title", { country: site.countryLabel }),
    description: site.t("dataset_body", { country: site.countryLabel }),
    url: `${site.base}/grants`,
    licence: site.t("footer_licence"),
    publisherName: "GoBuga",
    publisherUrl: site.base,
    spatialCoverage: site.countryLabel,
    dateModified: publishedAt,
    downloads: [
      { url: `${site.base}/grants.json`, encodingFormat: "application/json", name: site.t("dataset_json") },
      { url: `${site.base}/api/v1/opportunities`, encodingFormat: "application/json", name: site.t("dataset_api") },
    ],
  });
}

function Facets({ site, taxonomy }: { site: Site; taxonomy: Taxonomy }) {
  const carry = site.langs.carry;
  const groups: [string, string, [string, string][]][] = [
    ["tag", site.t("facet_tags"), tagOptions(taxonomy)],
    ["region", site.t("facet_regions"), regionOptions(taxonomy)],
    ["amount", site.t("facet_amounts"), amountOptions(site, taxonomy)],
    ["deadline", site.t("facet_deadlines"), deadlineOptions(site, taxonomy)],
    ["status", site.t("facet_status"), [["closed", site.t("opt_status.closed")], ["all", site.t("opt_status.all")]]],
  ];
  return (
    <section aria-labelledby="facets" className="mt-10">
      <h2 id="facets" className="text-2xl font-semibold mb-3">{site.t("facets_title")}</h2>
      {groups.map(([name, label, options]) => (
        <div key={name} className="mb-4">
          <h3 className="font-semibold">{label}</h3>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {options.map(([value, text]) => (
              <li key={value}><a href={facetHref(name, value, carry)}>{text}</a></li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

export function GrantIndex({ site, taxonomy, funders, values, result }: {
  site: Site;
  taxonomy: Taxonomy;
  funders: FunderSummary[];
  values: Record<string, string>;
  result: BackendResult<ListBody<GrantRecord>>;
}) {
  const t = site.t;
  const filtered = isFiltered(values);
  const body = result.body;
  const pages = body ? paginate(body.total, body.offset, body.limit, body.data.length) : null;
  const pageLink = (offset: number | null) =>
    site.link("/grants", { ...values, offset: offset || null, limit: values.limit });
  return (
    <div>
      <LanguageLinks site={site} path="/grants" params={values} />
      <h1 className="text-3xl font-bold mb-4">
        {t(filtered ? "grants_title_filtered" : "grants_title", { country: site.countryLabel })}
      </h1>

      <section aria-labelledby="filters" className="mb-6">
        <h2 id="filters" className="sr-only">{t("filters_title")}</h2>
        <GrantFilters site={site} taxonomy={taxonomy} funders={funders} values={values} />
        {filtered && <p className="mt-2"><a href={site.link("/grants")}>{t("filter_clear")}</a></p>}
      </section>

      {!body && <p role="alert" className="border-l-4 border-red-600 bg-red-50 p-3">{invalidMessage(site, result.error)}</p>}

      {body && (
        <section aria-labelledby="results">
          <h2 id="results" className="text-xl font-semibold">{t("results_count", { n: body.total })}</h2>
          {body.total === 0 ? (
            <p className="mt-2">
              {t(filtered ? "empty_filtered" : "empty_all")}{" "}
              {filtered && <a href={site.link("/grants")}>{t("see_all")}</a>}
            </p>
          ) : (
            <>
              {pages && pages.from > 0 && (
                <p className="text-slate-700">{t("showing", { from: pages.from, to: pages.to, total: body.total })}</p>
              )}
              <ul>
                {body.data.map((grant) => <GrantLine key={grant.id} site={site} grant={grant} />)}
              </ul>
            </>
          )}
          {pages && (pages.prevOffset !== null || pages.nextOffset !== null) && (
            <nav aria-label={t("pagination_label")} className="flex gap-6 mt-4">
              {pages.prevOffset !== null && <a href={pageLink(pages.prevOffset)} rel="prev">{t("page_prev")}</a>}
              {pages.nextOffset !== null && <a href={pageLink(pages.nextOffset)} rel="next">{t("page_next")}</a>}
            </nav>
          )}
        </section>
      )}

      <Facets site={site} taxonomy={taxonomy} />

      <section aria-labelledby="dataset" className="mt-10">
        <h2 id="dataset" className="text-2xl font-semibold mb-2">{t("dataset_title")}</h2>
        <p>{t("dataset_body", { country: site.countryLabel })}</p>
        {site.index.meta.published_at && (
          <p><Tx site={site} k="dataset_published" values={{ date: <InstantDate site={site} iso={site.index.meta.published_at} /> }} /></p>
        )}
        <p className="mt-2">
          <a href="/grants.json" type="application/json">{t("dataset_json")}</a>{" · "}
          <a href="/api/v1/opportunities" type="application/json">{t("dataset_api")}</a>
        </p>
      </section>

      <JsonLd data={datasetLd(site, site.index.meta.published_at)} />
    </div>
  );
}
