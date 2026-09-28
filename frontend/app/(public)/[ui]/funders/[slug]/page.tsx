// /funders/{slug}: one funder's open and closed grants. Cached and
// revalidated every ten minutes; an unknown slug is a 404.

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { LanguageLinks } from "../../../_ui/chrome";
import { GrantLine } from "../../../_ui/grant-bits";
import { BackendUnavailable, getCached } from "@/lib/backend";
import type { FunderBody } from "@/lib/public-types";
import { loadSite, pageMetadata } from "@/lib/site";

export const revalidate = 600;

export async function generateStaticParams() {
  return [];
}

type Props = { params: Promise<{ ui: string; slug: string }> };

async function loadFunder(slug: string): Promise<FunderBody | null> {
  const res = await getCached<FunderBody>(`/api/v1/funders/${encodeURIComponent(slug)}`);
  if (res.status === 404 || res.status === 400) return null;
  if (!res.body) throw new BackendUnavailable(`funder ${slug}: ${res.status}`);
  return res.body;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui, slug } = await params;
  const [site, funder] = await Promise.all([loadSite(ui), loadFunder(slug)]);
  if (!funder) return { title: site.t("funder_not_found_title"), robots: { index: false } };
  return pageMetadata(site, {
    path: `/funders/${funder.slug}`,
    title: funder.name,
    description: `${funder.name}. ${site.t("funder_counts", { live: funder.live_count, closed: funder.closed_count })}`,
    json: `/funders/${funder.slug}.json`,
  });
}

export default async function FunderRoute({ params }: Props) {
  const { ui, slug } = await params;
  const [site, funder] = await Promise.all([loadSite(ui), loadFunder(slug)]);
  if (!funder) notFound();
  const path = `/funders/${funder.slug}`;
  return (
    <div>
      <LanguageLinks site={site} path={path} />
      <h1 className="text-3xl font-bold mb-2">{funder.name}</h1>
      <p className="mb-6">{site.t("funder_counts", { live: funder.live_count, closed: funder.closed_count })}</p>

      <section aria-labelledby="live">
        <h2 id="live" className="text-2xl font-semibold">{site.t("funder_live")}</h2>
        {funder.live.length === 0 ? <p>{site.t("funder_none_live")}</p> : (
          <ul>{funder.live.map((g) => <GrantLine key={g.id} site={site} grant={g} />)}</ul>
        )}
      </section>

      <section aria-labelledby="closed" className="mt-8">
        <h2 id="closed" className="text-2xl font-semibold">{site.t("funder_closed")}</h2>
        {funder.closed.length === 0 ? <p>{site.t("funder_none_closed")}</p> : (
          <ul>{funder.closed.map((g) => <GrantLine key={g.id} site={site} grant={g} />)}</ul>
        )}
      </section>

      <p className="mt-6"><a href={`${path}.json`} type="application/json">{site.t("funder_json")}</a></p>
    </div>
  );
}
