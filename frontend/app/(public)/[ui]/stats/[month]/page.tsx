// /stats/{YYYY-MM}: one month's public counters, at a permanent URL.
// Revalidated every minute; a malformed or future month is a 404.

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { loadStats, StatsView } from "../../../_ui/stats-view";
import { formatMonth } from "@/lib/format";
import { loadSite, pageMetadata } from "@/lib/site";

export const revalidate = 60;

export async function generateStaticParams() {
  return [];
}

type Props = { params: Promise<{ ui: string; month: string }> };

const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui, month } = await params;
  const site = await loadSite(ui);
  if (!MONTH_RE.test(month)) return { title: site.t("stats_not_found_title"), robots: { index: false } };
  return pageMetadata(site, {
    path: `/stats/${month}`,
    title: site.t("stats_title_month", { month: formatMonth(month, site.locale) ?? month }),
    description: site.t("stats_intro"),
    json: `/stats/${month}.json`,
  });
}

export default async function StatsMonthRoute({ params }: Props) {
  const { ui, month } = await params;
  // Checked here as well as by the backend, so junk never reaches it.
  if (!MONTH_RE.test(month)) notFound();
  const [site, stats] = await Promise.all([loadSite(ui), loadStats(month)]);
  if (!stats) notFound();
  return <StatsView site={site} stats={stats} path={`/stats/${month}`} month={month} />;
}
