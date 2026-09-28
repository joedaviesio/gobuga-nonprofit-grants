"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  listCases,
  listOpportunities,
  openCaseFromPool,
  type CaseSummary,
  type OpportunitiesQuery,
  type OpportunityRow,
  type FitParams,
  type FitTaxonomy,
} from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import ErrorModal from "@/app/error-modal";
import CasesView, { isNewOpenCase } from "@/app/cases-view";
import { FitSummary, useFitTaxonomy } from "@/app/fit-fields";
import { localeFor } from "@/lib/format";
import { amountLabel, deadlineLabel, verifiedLabel } from "@/lib/grant-text";
import { regionLabel, tagLabel } from "@/lib/labels";
import { getDeploymentConfig } from "@/lib/countries";
import { useI18n } from "@/lib/i18n";

// Persists active sector chips across reloads. They start empty: the org's
// sectors rank the list ("Best fit") rather than filter it, since the chips
// are ANDed and would hide grants. v2: the old key held chips seeded from
// the profile.
const FILTER_TAGS_STORAGE_KEY = "gobuga_opp_filter_tags_v2";

type SortKey = "fit" | "recency" | "deadline" | "amount_desc" | "random";

function TagPill({ tag, label, active, onClick }: { tag: string; label: string; active: boolean; onClick?: () => void }) {
  const base = "text-sm px-2.5 py-0.5 rounded-full font-medium border transition-colors";
  const cls = active
    ? "bg-slate-900 text-white border-slate-900"
    : "bg-white text-slate-700 border-slate-200 hover:border-slate-400";
  return (
    <button onClick={onClick} className={`${base} ${cls}`} type="button" data-tag={tag}>
      {label}
    </button>
  );
}

/** One grant in a workspace list, worded as on the public pages. `why` lists
 * the reasons it fits the org ("Best fit"). */
export function OpportunityCard({ row, existing, opening, onOpen, why, taxonomy }: {
  row: OpportunityRow;
  existing?: CaseSummary;
  opening: boolean;
  onOpen: () => void;
  why?: string[];
  taxonomy: FitTaxonomy | null;
}) {
  const { t, lang } = useI18n();
  const config = getDeploymentConfig();
  const locale = localeFor(lang, config.country);
  const verified = verifiedLabel(row, lang, locale, config.timezone);
  return (
    <div
      className="card-gradient border border-slate-200 p-4 shadow-sm hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-base font-medium text-slate-800 font-[family-name:var(--font-dm-sans)]">
              {row.title}
            </span>
          </div>
          {/* Worded as on the public pages (lib/grant-text.ts) */}
          <p className="text-sm text-slate-700 mb-1">
            <span className="font-medium">{row.funder}</span>
            {" · "}{amountLabel(row, lang, locale)}
            {" · "}{deadlineLabel(row, lang, locale)}
            {verified && <>{" · "}{verified}</>}
          </p>
          {row.region.length > 0 && (
            <p className="text-sm text-slate-600 mb-2">
              {row.region.map(regionLabel).join(", ")}
            </p>
          )}
          {why && why.length > 0 && (
            <ul className="text-sm text-emerald-800 mb-2 list-disc pl-5">
              {why.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          {row.summary && (
            <p className="text-sm text-slate-600 line-clamp-2 mb-2">{row.summary}</p>
          )}
          <div className="flex flex-wrap gap-1">
            {row.tags.map((tag) => (
              <span
                key={tag}
                className="text-xs px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200"
              >
                {tagLabel(taxonomy, tag)}
              </span>
            ))}
          </div>
          {row.source_url && (
            <a
              href={row.source_url}
              target="_blank"
              rel="noopener"
              className="inline-block mt-2 text-sm text-blue-600 underline hover:text-blue-800"
            >
              {(() => {
                try { return new URL(row.source_url).hostname; }
                catch { return t("opps.source"); }
              })()}
            </a>
          )}
        </div>
        <div className="shrink-0">
          {existing ? (
            <a
              href={`/case/${existing.case_id}`}
              className="px-3 py-1.5 text-sm bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 transition-colors"
            >
              {t("opps.view_case")}
            </a>
          ) : (
            <button
              onClick={onOpen}
              disabled={opening}
              className="px-4 py-1.5 text-sm btn-gradient rounded-lg disabled:opacity-50 transition-colors"
            >
              {opening ? t("opps.opening") : t("opps.open_case")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function OpportunitiesView() {
  const { t } = useI18n();
  const [pool, setPool] = useState<OpportunityRow[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState<string | null>(null);
  const [errorModal, setErrorModal] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);

  // top-level tab
  const [tab, setTab] = useState<"opportunities" | "cases">("opportunities");
  const [fitParams, setFitParams] = useState<FitParams>({});
  const taxonomy = useFitTaxonomy();

  // filter state
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [tagsHydrated, setTagsHydrated] = useState(false);
  const [region, setRegion] = useState<string>("");
  const [sort, setSort] = useState<SortKey>("fit");
  const [cursor, setCursor] = useState(0);

  // Debounce free-text input → debouncedQ
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  // Restore activeTags on first mount from localStorage.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = localStorage.getItem(FILTER_TAGS_STORAGE_KEY);
    if (stored !== null) {
      try {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed)) {
          // Mount-time hydration from localStorage, readable only on the client
          // eslint-disable-next-line react-hooks/set-state-in-effect
          setActiveTags(parsed.filter((t): t is string => typeof t === "string"));
        }
      } catch {
        // bad JSON — ignore
      }
    }
    setTagsHydrated(true);
  }, []);

  // Persist activeTags whenever they change (after hydration, so we don't
  // overwrite stored state with the empty initial value).
  useEffect(() => {
    if (!tagsHydrated || typeof window === "undefined") return;
    localStorage.setItem(FILTER_TAGS_STORAGE_KEY, JSON.stringify(activeTags));
  }, [activeTags, tagsHydrated]);

  // Reset cursor when filters change
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCursor(0);
  }, [debouncedQ, activeTags, region, sort]);

  const query = useMemo<OpportunitiesQuery>(() => ({
    q: debouncedQ || undefined,
    tags: activeTags.length ? activeTags : undefined,
    region: region || undefined,
    sort,
    cursor,
    limit: 50,
  }), [debouncedQ, activeTags, region, sort, cursor]);

  const queryKey = JSON.stringify(query);
  const lastQueryRef = useRef<string>("");

  useEffect(() => {
    if (lastQueryRef.current === queryKey) return;
    lastQueryRef.current = queryKey;
    // Loading flag for the fetch this effect starts
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    listOpportunities(query)
      .then((res) => {
        setPool(res.opportunities);
        setTotal(res.total);
        setHasMore(res.has_more);
        setFitParams(res.fit_params ?? {});
      })
      .catch((err: unknown) => {
        setErrorModal(err instanceof Error ? err.message : t("opps.load_failed"));
      })
      .finally(() => setLoading(false));
  }, [queryKey, query]);

  useEffect(() => {
    listCases().then(setCases).catch(() => setCases([]));
  }, []);

  const toggleTag = (t: string) => {
    setActiveTags((curr) => curr.includes(t) ? curr.filter((x) => x !== t) : [...curr, t]);
  };

  const handleOpen = async (row: OpportunityRow) => {
    setOpening(row.id);
    try {
      const newCase = await openCaseFromPool(row.id);
      window.location.assign(`/case/${newCase.case_id}`);
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : t("opps.open_failed"));
      setOpening(null);
    }
  };

  const findExistingCase = (row: OpportunityRow): CaseSummary | undefined => {
    return cases.find((c) =>
      (c.grant_id || "").startsWith(
        row.title.toLowerCase().split(/\s+/).slice(0, 5).join("-").replace(/[^a-z0-9-]/g, "")
      )
    );
  };

  return (
    <div className="min-h-screen app-bg">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <ErrorModal message={errorModal} onClose={() => setErrorModal(null)} />

        {/* Top-level tabs */}
        <div className="flex items-center gap-4 mb-6 border-b border-slate-200 pb-3">
          <button
            onClick={() => setTab("opportunities")}
            className={`text-base font-medium pb-1 transition-colors ${
              tab === "opportunities"
                ? "text-slate-900 border-b-2 border-slate-900"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            {t("opps.tab_opportunities")}
          </button>
          <button
            onClick={() => setTab("cases")}
            className={`text-base font-medium pb-1 transition-colors ${
              tab === "cases"
                ? "text-slate-900 border-b-2 border-slate-900"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            {t("opps.tab_cases")} ({cases.length})
            {(() => {
              const newOpen = cases.filter(isNewOpenCase).length;
              return newOpen > 0 ? (
                <span className="ml-1 inline-flex items-center justify-center h-4 min-w-[16px] px-1 text-xs font-bold text-white bg-red-500 rounded-full">
                  {newOpen}
                </span>
              ) : null;
            })()}
          </button>
        </div>

        {tab === "cases" && <CasesView cases={cases} />}

        {tab === "opportunities" && (
        <>

        {/* Header / search */}
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900 mb-3 font-[family-name:var(--font-dm-sans)]">
            {t("opps.title")}
          </h1>
          <div className="relative">
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("opps.search_placeholder")}
              className="w-full pl-10 pr-4 py-3 text-base border border-slate-200 rounded-xl bg-white focus:outline-none focus:border-blue-400 shadow-sm"
            />
            <svg
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="8" />
              <path d="m21 21-4.3-4.3" />
            </svg>
          </div>
        </div>

        {/* Tag pills */}
        <div className="mb-3 flex flex-wrap gap-1.5">
          {getDeploymentConfig().tags.map((t) => (
            <TagPill key={t} tag={t} label={tagLabel(taxonomy, t)} active={activeTags.includes(t)} onClick={() => toggleTag(t)} />
          ))}
        </div>

        {/* Region + sort */}
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <label className="text-sm text-slate-600">
            {t("opps.region")}:{" "}
            <select
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className="text-sm border border-slate-200 rounded px-2 py-1 bg-white"
            >
              <option value="">{t("opps.any")}</option>
              {getDeploymentConfig().regionSlugs.map((r) => (
                <option key={r} value={r}>{regionLabel(r)}</option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-600">
            {t("opps.sort")}:{" "}
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="text-sm border border-slate-200 rounded px-2 py-1 bg-white"
            >
              <option value="fit">{t("opps.sort_fit")}</option>
              <option value="recency">{t("opps.sort_recency")}</option>
              <option value="deadline">{t("opps.sort_deadline")}</option>
              <option value="amount_desc">{t("opps.sort_amount")}</option>
              <option value="random">{t("opps.sort_random")}</option>
            </select>
          </label>
          <span className="ml-auto text-sm text-slate-600">
            {loading ? t("opps.loading") : t("opps.n_matching", { n: total })}
          </span>
        </div>

        {/* What "Best fit" ranks on */}
        {sort === "fit" && !loading && (
          Object.keys(fitParams).length > 0 ? (
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="text-sm text-slate-600">{t("opps.fit_ranked_on")}</span>
              <FitSummary params={fitParams} />
              <a href="/settings" className="text-sm text-blue-600 underline hover:text-blue-800">
                {t("opps.fit_edit_profile")}
              </a>
            </div>
          ) : (
            <p className="mb-4 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              {t("opps.fit_no_profile")}{" "}
              <a href="/settings" className="underline">{t("opps.fit_edit_profile")}</a>
            </p>
          )
        )}

        {/* List */}
        {loading && pool.length === 0 ? (
          <LoadingBar label={t("opps.loading_opportunities")} />
        ) : pool.length === 0 ? (
          <div className="text-center py-16 text-base text-slate-600">
            {t("opps.no_matches")}
            {(q || activeTags.length || region) && (
              <div className="mt-3">
                <button
                  onClick={() => { setQ(""); setActiveTags([]); setRegion(""); }}
                  className="text-sm px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-800"
                >
                  {t("opps.clear_filters")}
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {pool.map((row) => {
              return (
                <OpportunityCard
                  key={row.id}
                  row={row}
                  why={sort === "fit" ? row.why : undefined}
                  taxonomy={taxonomy}
                  existing={findExistingCase(row)}
                  opening={opening === row.id}
                  onOpen={() => handleOpen(row)}
                />
              );
            })}
          </div>
        )}

        {/* Pagination */}
        {(cursor > 0 || hasMore) && pool.length > 0 && (
          <div className="mt-6 flex items-center justify-between text-sm">
            <button
              onClick={() => setCursor(Math.max(0, cursor - 50))}
              disabled={cursor === 0}
              className="px-3 py-1.5 border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50"
            >
              ← {t("common.previous")}
            </button>
            <span className="text-slate-600">
              {t("opps.showing_range", { from: cursor + 1, to: cursor + pool.length, total })}
            </span>
            <button
              onClick={() => setCursor(cursor + 50)}
              disabled={!hasMore}
              className="px-3 py-1.5 border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50"
            >
              {t("common.next")} →
            </button>
          </div>
        )}
        </>
        )}
      </div>
    </div>
  );
}
