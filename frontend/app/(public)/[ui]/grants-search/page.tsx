// A filtered or paginated /grants?tag=...&offset=... . proxy.ts sends those
// requests here (visitors never see this path). Rendered on request; the
// backend response is held in a bounded cache (lib/backend.ts getByQuery),
// because the filter combinations, free-text `q` included, are unbounded.

import type { Metadata } from "next";
import { GrantIndex, indexMetadata, loadFunders } from "../../_ui/grant-index";
import { getByQuery } from "@/lib/backend";
import { INDEX_PARAMS } from "@/lib/public-routes";
import type { GrantRecord, ListBody } from "@/lib/public-types";
import { pick, queryString, type SearchParams } from "@/lib/public-urls";
import { loadSite, loadTaxonomy } from "@/lib/site";

// Reads the query string, so it is rendered on every request.
export const dynamic = "force-dynamic";

type Props = { params: Promise<{ ui: string }>; searchParams: Promise<SearchParams> };

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const { ui } = await params;
  return indexMetadata(await loadSite(ui), pick(await searchParams, INDEX_PARAMS));
}

export default async function GrantsSearchRoute({ params, searchParams }: Props) {
  const { ui } = await params;
  const values = pick(await searchParams, INDEX_PARAMS);
  const [site, taxonomy, funders, result] = await Promise.all([
    loadSite(ui), loadTaxonomy(), loadFunders(),
    getByQuery<ListBody<GrantRecord>>(`/api/v1/opportunities?${queryString(values)}`),
  ]);
  return <GrantIndex site={site} taxonomy={taxonomy} funders={funders} values={values} result={result} />;
}
