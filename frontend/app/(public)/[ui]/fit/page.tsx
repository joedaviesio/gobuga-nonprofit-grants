// /fit?sector=&region=&status=&size=&need= : grants ranked for an
// organisation described by five parameters, each with the reasons it is
// listed. Rendered on request (the parameter combinations cannot be built
// ahead); the backend response is held in a bounded cache
// (lib/backend.ts getByQuery). A bad value shows the form again with a
// message naming the field. The status stays 200: an App Router page cannot
// set a 400 without throwing.

import type { Metadata } from "next";
import { LanguageLinks } from "../../_ui/chrome";
import { FitForm, invalidMessage } from "../../_ui/forms";
import { GrantLine } from "../../_ui/grant-bits";
import { getByQuery, type BackendResult } from "@/lib/backend";
import { FIT_PARAMS } from "@/lib/public-routes";
import type { FitBody } from "@/lib/public-types";
import { grantHref, href, paginate, pick, queryString, type SearchParams } from "@/lib/public-urls";
import { loadSite, loadTaxonomy, pageMetadata, type Site } from "@/lib/site";

// Reads the query string, so it is rendered on every request.
export const dynamic = "force-dynamic";

type Props = { params: Promise<{ ui: string }>; searchParams: Promise<SearchParams> };

const PAGING = ["offset", "limit"] as const;

async function loadFit(search: SearchParams) {
  const fit = pick(search, FIT_PARAMS);
  const paging = pick(search, PAGING);
  const result = await getByQuery<FitBody>(`/api/v1/fit?${queryString({ ...fit, ...paging })}`);
  return { fit, paging, result };
}

/** The fit parameters in the API's canonical form, e.g. {sector: "sport", region: "otago"}. */
function canonicalParams(result: BackendResult<FitBody>): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(result.body?.canonical_query ?? ""));
}

function canonicalUrl(site: Site, result: BackendResult<FitBody>, paging: Record<string, string>): string | null {
  if (!result.body) return null;
  return site.base + href("/fit", { ...canonicalParams(result), ...paging }, site.langs.carry);
}

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const { ui } = await params;
  const [site, { result, paging }] = await Promise.all([loadSite(ui), searchParams.then(loadFit)]);
  const query = result.body?.canonical_query ?? "";
  return pageMetadata(site, {
    path: "/fit",
    title: site.t("fit_title"),
    description: site.t("fit_intro"),
    json: query ? `/fit.json?${query}` : "/fit.json",
    atom: query ? `/fit/feed.xml?${query}` : "/fit/feed.xml",
    // Points at the canonical form when the URL is not in it (order, case, labels).
    canonical: canonicalUrl(site, result, paging),
    noindex: !result.body,
  });
}

export default async function FitRoute({ params, searchParams }: Props) {
  const { ui } = await params;
  const [site, taxonomy, { fit, paging, result }] = await Promise.all([
    loadSite(ui), loadTaxonomy(), searchParams.then(loadFit),
  ]);
  const t = site.t;
  const body = result.body;
  const carryFit = canonicalParams(result);
  const pages = body ? paginate(body.total, body.offset, body.limit, body.data.length) : null;
  const pageLink = (offset: number) => site.link("/fit", { ...carryFit, offset: offset || null, limit: paging.limit });

  return (
    <div>
      <LanguageLinks site={site} path="/fit" params={{ ...fit, ...paging }} />
      <h1 className="text-3xl font-bold mb-2">{t("fit_title")}</h1>
      <p className="mb-4">{t("fit_intro")}</p>
      <FitForm site={site} taxonomy={taxonomy} values={body ? carryFit : fit} />

      {!body && (
        <p role="alert" className="mt-4 border-l-4 border-red-600 bg-red-50 p-3">{invalidMessage(site, result.error, true)}</p>
      )}

      {body && (
        <>
          <section aria-labelledby="results" className="mt-6">
            <h2 id="results" className="text-xl font-semibold">{t("fit_results", { n: body.total })}</h2>
            {body.total === 0 ? <p>{t("fit_empty")}</p> : (
              <ol start={body.offset + 1} className="list-decimal pl-6">
                {body.data.map((item) => (
                  <GrantLine
                    key={item.id}
                    site={site}
                    grant={item}
                    hrefFor={(id) => grantHref(id, carryFit, site.langs.carry)}
                    extra={item.why.length > 0 && (
                      <div className="mt-1">
                        <p className="text-sm font-semibold">{t("fit_why")}</p>
                        <ul className="list-disc pl-6 text-sm">
                          {item.why.map((reason) => <li key={reason}>{reason}</li>)}
                        </ul>
                      </div>
                    )}
                  />
                ))}
              </ol>
            )}
            {pages && (pages.prevOffset !== null || pages.nextOffset !== null) && (
              <nav aria-label={t("pagination_label")} className="flex gap-6 mt-4">
                {pages.prevOffset !== null && <a href={pageLink(pages.prevOffset)} rel="prev">{t("page_prev")}</a>}
                {pages.nextOffset !== null && <a href={pageLink(pages.nextOffset)} rel="next">{t("page_next")}</a>}
              </nav>
            )}
          </section>

          <section aria-labelledby="follow" className="mt-8">
            <h2 id="follow" className="text-xl font-semibold">{t("fit_follow_title")}</h2>
            <ul className="list-disc pl-6">
              <li><a href={body.feed_url} type="application/atom+xml">{t("fit_feed")}</a>: <code className="break-all">{body.feed_url}</code></li>
              <li><a href={body.json_url} type="application/json">{t("fit_json")}</a>: <code className="break-all">{body.json_url}</code></li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
