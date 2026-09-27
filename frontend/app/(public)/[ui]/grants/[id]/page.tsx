// /grants/{id}: the canonical grant page. Cached and revalidated every ten
// minutes; a request carrying fit parameters is sent to grant-fit/[id]
// instead by proxy.ts, so this page never reads the query string.

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { GrantPage, grantMetadata, loadGrant } from "../../../_ui/grant-page";
import { loadSite, loadTaxonomy } from "@/lib/site";

export const revalidate = 600;

export async function generateStaticParams() {
  return [];
}

type Props = { params: Promise<{ ui: string; id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui, id } = await params;
  const [site, grant] = await Promise.all([loadSite(ui), loadGrant(id)]);
  if (!grant) return { title: site.t("grant_not_found_title"), robots: { index: false } };
  return grantMetadata(site, grant);
}

export default async function GrantRoute({ params }: Props) {
  const { ui, id } = await params;
  const [site, grant, taxonomy] = await Promise.all([loadSite(ui), loadGrant(id), loadTaxonomy()]);
  if (!grant) notFound();
  return <GrantPage site={site} grant={grant} taxonomy={taxonomy} />;
}
