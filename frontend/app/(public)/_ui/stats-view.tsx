// The public counters (/stats and /stats/{month}) as plain tables and
// definition lists, each with one sentence on what is counted.

import { LanguageLinks } from "./chrome";
import { InstantDate, Tx } from "./grant-bits";
import { getCached } from "@/lib/backend";
import { formatMonth, previousMonths } from "@/lib/format";
import type { PublicKey } from "@/lib/public-i18n";
import type { StatsBody } from "@/lib/public-types";
import type { Site } from "@/lib/site";

/** Counters move with every request; the backend serves them with max-age 60. */
export const STATS_REVALIDATE = 60;

/** The counters for `month` (YYYY-MM), or the current month; null for a 400 or 404. */
export async function loadStats(month: string | null): Promise<StatsBody | null> {
  const res = await getCached<StatsBody>(month ? `/api/v1/stats/${encodeURIComponent(month)}` : "/api/v1/stats", STATS_REVALIDATE);
  return res.body;
}

function CountTable({ site, caption, column, rows, total }: {
  site: Site;
  caption: string;
  column: string;
  rows: [string, number][];
  total: number;
}) {
  const nf = new Intl.NumberFormat(site.locale);
  return (
    <table className="counts mb-2">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr><th scope="col">{column}</th><th scope="col">{site.t("stats_col_count")}</th></tr>
      </thead>
      <tbody>
        {rows.map(([label, n]) => (
          <tr key={label}><th scope="row" className="font-normal">{label}</th><td className="num">{nf.format(n)}</td></tr>
        ))}
      </tbody>
      <tfoot>
        <tr><th scope="row">{site.t("stats_total")}</th><td className="num">{nf.format(total)}</td></tr>
      </tfoot>
    </table>
  );
}

function Block({ id, title, note, children }: { id: string; title: string; note: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-8">
      <h3 id={id} className="text-xl font-semibold">{title}</h3>
      <p className="text-slate-700 mb-2">{note}</p>
      {children}
    </section>
  );
}

export function StatsView({ site, stats, path, month }: { site: Site; stats: StatsBody; path: string; month: string | null }) {
  const t = site.t;
  const u = stats.usage;
  const monthText = formatMonth(u.month, site.locale) ?? u.month;
  const nf = new Intl.NumberFormat(site.locale);
  const d = stats.dataset;
  return (
    <div>
      <LanguageLinks site={site} path={path} />
      <h1 className="text-3xl font-bold mb-2">{month ? t("stats_title_month", { month: monthText }) : t("stats_title")}</h1>
      <p className="mb-2">{t("stats_intro")}</p>
      <p className="text-sm text-slate-700">
        <Tx site={site} k="stats_counted_at" values={{ date: <InstantDate site={site} iso={stats.generated_at} /> }} />
      </p>

      {d && (
        <section aria-labelledby="dataset" className="mt-8">
          <h2 id="dataset" className="text-2xl font-semibold">{t("stats_dataset")}</h2>
          <p className="text-slate-700 mb-2">{t("stats_dataset_note")}</p>
          <dl className="facts">
            <dt>{t("count_live")}</dt><dd>{nf.format(d.live_count)}</dd>
            <dt>{t("count_closed")}</dt><dd>{nf.format(d.closed_count)}</dd>
            <dt>{t("count_funders")}</dt><dd>{nf.format(d.funder_count)}</dd>
            <dt>{t("count_last_sweep")}</dt><dd>{formatMonth(d.last_sweep, site.locale) ?? t("not_stated")}</dd>
            <dt>{t("count_published")}</dt><dd>{d.published_at ? <InstantDate site={site} iso={d.published_at} /> : t("not_stated")}</dd>
            <dt>{t("stats_next_sweep")}</dt><dd>{formatMonth(d.next_sweep_due, site.locale) ?? t("not_stated")}</dd>
            {stats.subscribers && (
              <>
                <dt>{t("stats_subscribers")}</dt><dd>{nf.format(stats.subscribers.confirmed)}</dd>
              </>
            )}
          </dl>
          {stats.subscribers && <p className="text-sm text-slate-700 mt-2">{t("stats_subscribers_note")}</p>}
        </section>
      )}

      <section aria-labelledby="usage" className="mt-8">
        <h2 id="usage" className="text-2xl font-semibold">{t("stats_usage", { month: monthText })}</h2>
        <Block id="clickouts" title={t("stats_clickouts")} note={t("stats_clickouts_note")}>
          <CountTable site={site} caption={t("stats_clickouts")} column={t("stats_col_source")}
            rows={Object.entries(u.clickouts.by_referrer).map(([k, n]) => {
              const key = `ref.${k}` as PublicKey;
              return [t(key) === key ? k : t(key), n];
            })}
            total={u.clickouts.total} />
        </Block>
        {u.fit_urls_built !== undefined && (
          <Block id="fit" title={t("stats_fit")} note={t("stats_fit_note")}>
            <dl className="facts"><dt>{t("stats_total")}</dt><dd>{nf.format(u.fit_urls_built)}</dd></dl>
          </Block>
        )}
        <Block id="api" title={t("stats_api")} note={t("stats_api_note")}>
          <CountTable site={site} caption={t("stats_api")} column={t("stats_col_endpoint")}
            rows={Object.entries(u.api_calls.by_endpoint)} total={u.api_calls.total} />
        </Block>
        <Block id="mcp" title={t("stats_mcp")} note={t("stats_mcp_note")}>
          <CountTable site={site} caption={t("stats_mcp")} column={t("stats_col_tool")}
            rows={Object.entries(u.mcp_calls.by_tool)} total={u.mcp_calls.total} />
        </Block>
      </section>

      <section aria-labelledby="months" className="mt-8">
        <h2 id="months" className="text-xl font-semibold">{t("stats_months")}</h2>
        <ul className="flex flex-wrap gap-x-4">
          {month && <li><a href={site.link("/stats")}>{t("stats_title")}</a></li>}
          {previousMonths(u.month).map((m) => (
            <li key={m}><a href={site.link(`/stats/${m}`)}>{formatMonth(m, site.locale) ?? m}</a></li>
          ))}
        </ul>
        <p className="mt-4"><a href={`${path}.json`} type="application/json">{t("stats_json")}</a></p>
      </section>
    </div>
  );
}
