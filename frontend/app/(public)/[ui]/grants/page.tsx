// /grants with no filter: the first page of open grants, the filter form,
// facet links and the Dataset description. Cached and revalidated every ten
// minutes. A filtered or paginated /grants is sent to grants-search by
// proxy.ts, so this page never reads the query string.

import type { Metadata } from "next";
import { GrantIndex, indexMetadata, loadFunders } from "../../_ui/grant-index";
import { BackendUnavailable, getCached } from "@/lib/backend";
import type { GrantRecord, ListBody } from "@/lib/public-types";
import { loadSite, loadTaxonomy } from "@/lib/site";

export const revalidate = 600;

type Props = { params: Promise<{ ui: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui } = await params;
  return indexMetadata(await loadSite(ui), {});
}

export default async function GrantsRoute({ params }: Props) {
  const { ui } = await params;
  const [site, taxonomy, funders, result] = await Promise.all([
    loadSite(ui), loadTaxonomy(), loadFunders(), getCached<ListBody<GrantRecord>>("/api/v1/opportunities"),
  ]);
  if (!result.body) throw new BackendUnavailable(`grant index: ${result.status}`);
  return <GrantIndex site={site} taxonomy={taxonomy} funders={funders} values={{}} result={result} />;
}
