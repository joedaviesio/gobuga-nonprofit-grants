// /funders: every funder with an open or closed grant, with counts.
// Cached and revalidated every ten minutes.

import type { Metadata } from "next";
import { LanguageLinks } from "../../_ui/chrome";
import { loadFunders } from "../../_ui/grant-index";
import { loadSite, pageMetadata } from "@/lib/site";

export const revalidate = 600;

type Props = { params: Promise<{ ui: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui } = await params;
  const site = await loadSite(ui);
  return pageMetadata(site, {
    path: "/funders",
    title: site.t("funders_title", { country: site.countryLabel }),
    description: site.t("funders_intro"),
    json: "/funders.json",
  });
}

export default async function FundersRoute({ params }: Props) {
  const { ui } = await params;
  const [site, funders] = await Promise.all([loadSite(ui), loadFunders()]);
  return (
    <div>
      <LanguageLinks site={site} path="/funders" />
      <h1 className="text-3xl font-bold mb-2">{site.t("funders_title", { country: site.countryLabel })}</h1>
      <p className="mb-4">{site.t("funders_intro")}</p>
      {funders.length === 0 ? (
        <p>{site.t("funders_empty")}</p>
      ) : (
        <ul>
          {funders.map((f) => (
            <li key={f.slug} className="py-2 border-b border-slate-200">
              <a href={site.link(`/funders/${f.slug}`)} className="font-semibold">{f.name}</a>
              {" "}
              <span className="text-slate-700">{site.t("funder_counts", { live: f.live_count, closed: f.closed_count })}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-4"><a href="/funders.json" type="application/json">{site.t("json_link")}</a></p>
    </div>
  );
}
