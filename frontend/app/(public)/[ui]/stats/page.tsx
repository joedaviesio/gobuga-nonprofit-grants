// /stats: this month's public counters. Revalidated every minute, matching
// the backend's own max-age for counters.

import type { Metadata } from "next";
import { loadStats, StatsView } from "../../_ui/stats-view";
import { BackendUnavailable } from "@/lib/backend";
import { loadSite, pageMetadata } from "@/lib/site";

export const revalidate = 60;

type Props = { params: Promise<{ ui: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui } = await params;
  const site = await loadSite(ui);
  return pageMetadata(site, {
    path: "/stats",
    title: site.t("stats_title"),
    description: site.t("stats_intro"),
    json: "/stats.json",
  });
}

export default async function StatsRoute({ params }: Props) {
  const { ui } = await params;
  const [site, stats] = await Promise.all([loadSite(ui), loadStats(null)]);
  if (!stats) throw new BackendUnavailable("stats missing");
  return <StatsView site={site} stats={stats} path="/stats" month={null} />;
}
