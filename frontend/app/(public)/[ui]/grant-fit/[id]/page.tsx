// A grant page reached from a fit URL: /grants/{id}?sector=...&region=...
// proxy.ts sends those requests here (visitors never see this path). The
// page is the canonical grant page plus the "why this fits" reasons for the
// profile in the query, and its canonical URL is the bare /grants/{id}.
// Rendered on request; the backend responses are held in a bounded cache
// (lib/backend.ts getByQuery).

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { GrantPage, grantMetadata, loadGrant, type FitReasons } from "../../../_ui/grant-page";
import { invalidMessage } from "../../../_ui/forms";
import { getByQuery } from "@/lib/backend";
import { FIT_PARAMS } from "@/lib/public-routes";
import type { FitBody } from "@/lib/public-types";
import { pick, queryString, type SearchParams } from "@/lib/public-urls";
import { loadSite, loadTaxonomy, type Site } from "@/lib/site";

// Reads the query string, so it is rendered on every request.
export const dynamic = "force-dynamic";

type Props = { params: Promise<{ ui: string; id: string }>; searchParams: Promise<SearchParams> };

const PAGE = 100;       // the API's largest page
const MAX_PAGES = 20;   // stop looking after 2,000 ranked rows

async function reasonsFor(site: Site, id: string, fitParams: Record<string, string>): Promise<FitReasons> {
  const query = queryString(fitParams);
  for (let page = 0; page < MAX_PAGES; page++) {
    const res = await getByQuery<FitBody>(`/api/v1/fit?${query}&offset=${page * PAGE}&limit=${PAGE}`);
    if (!res.body) {
      return { why: null, error: invalidMessage(site, res.error, true), params: fitParams };
    }
    const item = res.body.data.find((row) => row.id === id);
    if (item) return { why: item.why, error: null, params: fitParams };
    if ((page + 1) * PAGE >= res.body.total) break;
  }
  return { why: null, error: null, params: fitParams };
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui, id } = await params;
  const [site, grant] = await Promise.all([loadSite(ui), loadGrant(id)]);
  if (!grant) return { title: site.t("grant_not_found_title"), robots: { index: false } };
  // Same metadata as the bare page, so the canonical is /grants/{id}.
  return grantMetadata(site, grant);
}

export default async function GrantFitRoute({ params, searchParams }: Props) {
  const { ui, id } = await params;
  const fitParams = pick(await searchParams, FIT_PARAMS);
  const [site, grant, taxonomy] = await Promise.all([loadSite(ui), loadGrant(id), loadTaxonomy()]);
  if (!grant) notFound();
  const fit = await reasonsFor(site, grant.id, fitParams);
  return <GrantPage site={site} grant={grant} taxonomy={taxonomy} fit={fit} />;
}
