"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  listCases,
  listOpportunities,
  openCaseFromPool,
  type CaseSummary,
  type OpportunitiesQuery,
  type OpportunityRow,
} from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import ErrorModal from "@/app/error-modal";
import CasesView, { isNewOpenCase } from "@/app/cases-view";
import TailoredView from "@/app/tailored-view";
import { getTailoredAccess, type TailoredAccess } from "@/lib/api";

const ALL_TAGS = [
  "community", "sport", "civic", "arts", "environment", "education",
  "health", "youth", "maori", "pasifika", "research", "capability",
  "infrastructure", "business", "women", "disability", "climate",
] as const;

const NZ_REGIONS = [
  "national", "auckland", "wellington", "canterbury", "otago", "waikato",
  "bay-of-plenty", "northland", "taranaki", "manawatu-whanganui",
  "hawkes-bay", "tasman", "nelson", "marlborough", "west-coast",
  "southland", "gisborne",
] as const;

type SortKey = "recency" | "deadline" | "amount_desc";

function formatAmount(row: OpportunityRow): string | null {
  const lo = row.amount_min;
  const hi = row.amount_max;
  const ccy = row.currency || "NZD";
  if (lo && hi && lo !== hi) return `$${lo.toLocaleString()}–$${hi.toLocaleString()} ${ccy}`;
  if (hi) return `up to $${hi.toLocaleString()} ${ccy}`;
  if (lo) return `from $${lo.toLocaleString()} ${ccy}`;
  return null;
}

function DeadlineBadge({ deadline }: { deadline: string | null }) {
  if (!deadline || deadline === "TBC") {
    return <span className="text-xs text-slate-500">deadline TBC</span>;
  }
  if (deadline === "rolling") {
    return <span className="text-xs text-slate-600">rolling</span>;
  }
  // Try to render days-until
  const dt = new Date(deadline);
  if (!isNaN(dt.getTime())) {
    const days = Math.ceil((dt.getTime() - Date.now()) / 86400000);
    const tone = days < 7 ? "text-red-600" : days < 30 ? "text-amber-600" : "text-slate-600";
    return (
      <span className={`text-xs ${tone}`}>
        closes {dt.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
        {days >= 0 && days <= 60 ? ` · ${days}d` : ""}
      </span>
    );
  }
  return <span className="text-xs text-slate-600">{deadline}</span>;
}

function TagPill({ tag, active, onClick }: { tag: string; active: boolean; onClick?: () => void }) {
  const base = "text-xs px-2.5 py-0.5 rounded-full font-medium border transition-colors";
  const cls = active
    ? "bg-slate-900 text-white border-slate-900"
    : "bg-white text-slate-700 border-slate-200 hover:border-slate-400";
  return (
    <button onClick={onClick} className={`${base} ${cls}`} type="button">
      {tag}
    </button>
  );
}

export default function OpportunitiesView() {
  const [pool, setPool] = useState<OpportunityRow[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState<string | null>(null);
  const [errorModal, setErrorModal] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);

  // top-level tab
  const [tab, setTab] = useState<"opportunities" | "tailored" | "cases">("opportunities");
  const [tailoredAccess, setTailoredAccess] = useState<TailoredAccess | null>(null);

  // filter state
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [region, setRegion] = useState<string>("");
  const [sort, setSort] = useState<SortKey>("recency");
  const [cursor, setCursor] = useState(0);

  // Debounce free-text input → debouncedQ
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  // Reset cursor when filters change
  useEffect(() => {
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
    setLoading(true);
    listOpportunities(query)
      .then((res) => {
        setPool(res.opportunities);
        setTotal(res.total);
        setHasMore(res.has_more);
      })
      .catch((err: unknown) => {
        setErrorModal(err instanceof Error ? err.message : "Failed to load opportunities.");
      })
      .finally(() => setLoading(false));
  }, [queryKey, query]);

  useEffect(() => {
    listCases().then(setCases).catch(() => setCases([]));
    getTailoredAccess().then(setTailoredAccess).catch(() => setTailoredAccess(null));
  }, []);

  // Show the tab for everyone EXCEPT Officer tier with toggle deliberately off
  // (which is the only case where the user has explicitly opted out).
  const showTailoredTab =
    !!tailoredAccess && tailoredAccess.reason !== "disabled";

  const toggleTag = (t: string) => {
    setActiveTags((curr) => curr.includes(t) ? curr.filter((x) => x !== t) : [...curr, t]);
  };

  const handleOpen = async (row: OpportunityRow) => {
    setOpening(row.id);
    try {
      const newCase = await openCaseFromPool(row.id);
      window.location.href = `/case/${newCase.case_id}`;
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : "Failed to open case.");
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
            className={`text-sm font-medium pb-1 transition-colors ${
              tab === "opportunities"
                ? "text-slate-900 border-b-2 border-slate-900"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            Opportunities
          </button>
          {showTailoredTab && (
            <button
              onClick={() => setTab("tailored")}
              className={`text-sm font-medium pb-1 transition-colors ${
                tab === "tailored"
                  ? "text-slate-900 border-b-2 border-slate-900"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Tailored Picks
            </button>
          )}
          <button
            onClick={() => setTab("cases")}
            className={`text-sm font-medium pb-1 transition-colors ${
              tab === "cases"
                ? "text-slate-900 border-b-2 border-slate-900"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            Cases ({cases.length})
            {(() => {
              const newOpen = cases.filter(isNewOpenCase).length;
              return newOpen > 0 ? (
                <span className="ml-1 inline-flex items-center justify-center h-4 min-w-[16px] px-1 text-[10px] font-bold text-white bg-red-500 rounded-full">
                  {newOpen}
                </span>
              ) : null;
            })()}
          </button>
        </div>

        {tab === "cases" && <CasesView cases={cases} />}

        {tab === "tailored" && showTailoredTab && <TailoredView />}

        {tab === "opportunities" && (
        <>

        {/* Header / search */}
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-slate-900 mb-3 font-[family-name:var(--font-dm-sans)]">
            Grant opportunities
          </h1>
          <div className="relative">
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search funders, tags, keywords…"
              className="w-full pl-10 pr-4 py-3 text-sm border border-slate-200 rounded-xl bg-white focus:outline-none focus:border-blue-400 shadow-sm"
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
          {ALL_TAGS.map((t) => (
            <TagPill key={t} tag={t} active={activeTags.includes(t)} onClick={() => toggleTag(t)} />
          ))}
        </div>

        {/* Region + sort */}
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <label className="text-xs text-slate-600">
            Region:{" "}
            <select
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className="text-xs border border-slate-200 rounded px-2 py-1 bg-white"
            >
              <option value="">any</option>
              {NZ_REGIONS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-600">
            Sort:{" "}
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="text-xs border border-slate-200 rounded px-2 py-1 bg-white"
            >
              <option value="recency">recency</option>
              <option value="deadline">deadline</option>
              <option value="amount_desc">amount (high→low)</option>
            </select>
          </label>
          <span className="ml-auto text-xs text-slate-600">
            {loading ? "loading…" : `${total} matching opportunit${total === 1 ? "y" : "ies"}`}
          </span>
        </div>

        {/* List */}
        {loading && pool.length === 0 ? (
          <LoadingBar label="Loading opportunities…" />
        ) : pool.length === 0 ? (
          <div className="text-center py-16 text-sm text-slate-600">
            No opportunities match those filters yet.
            {(q || activeTags.length || region) && (
              <div className="mt-3">
                <button
                  onClick={() => { setQ(""); setActiveTags([]); setRegion(""); }}
                  className="text-xs px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-800"
                >
                  Clear filters
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {pool.map((row) => {
              const amt = formatAmount(row);
              const existing = findExistingCase(row);
              return (
                <div
                  key={row.id}
                  className="card-gradient border border-slate-200 p-4 shadow-sm hover:shadow-md transition-shadow"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className="text-sm font-medium text-slate-800 font-[family-name:var(--font-dm-sans)]">
                          {row.title}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-700 mb-2">
                        <span className="font-medium">{row.funder}</span>
                        <DeadlineBadge deadline={row.deadline} />
                        {amt && <span>{amt}</span>}
                        {row.region.length > 0 && (
                          <span className="text-slate-600">
                            {row.region.includes("national") ? "national" : row.region.join(", ")}
                          </span>
                        )}
                      </div>
                      {row.summary && (
                        <p className="text-xs text-slate-600 line-clamp-2 mb-2">{row.summary}</p>
                      )}
                      <div className="flex flex-wrap gap-1">
                        {row.tags.map((t) => (
                          <span
                            key={t}
                            className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                      {row.source_url && (
                        <a
                          href={row.source_url}
                          target="_blank"
                          rel="noopener"
                          className="inline-block mt-2 text-xs text-blue-600 underline hover:text-blue-800"
                        >
                          {(() => {
                            try { return new URL(row.source_url).hostname; }
                            catch { return "Source"; }
                          })()}
                        </a>
                      )}
                    </div>
                    <div className="shrink-0">
                      {existing ? (
                        <a
                          href={`/case/${existing.case_id}`}
                          className="px-3 py-1.5 text-xs bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 transition-colors"
                        >
                          View case
                        </a>
                      ) : (
                        <button
                          onClick={() => handleOpen(row)}
                          disabled={opening === row.id}
                          className="px-4 py-1.5 text-xs btn-gradient rounded-lg disabled:opacity-50 transition-colors"
                        >
                          {opening === row.id ? "Opening…" : "Open case"}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Pagination */}
        {(cursor > 0 || hasMore) && pool.length > 0 && (
          <div className="mt-6 flex items-center justify-between text-xs">
            <button
              onClick={() => setCursor(Math.max(0, cursor - 50))}
              disabled={cursor === 0}
              className="px-3 py-1.5 border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50"
            >
              ← Previous
            </button>
            <span className="text-slate-600">
              Showing {cursor + 1}–{cursor + pool.length} of {total}
            </span>
            <button
              onClick={() => setCursor(cursor + 50)}
              disabled={!hasMore}
              className="px-3 py-1.5 border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50"
            >
              Next →
            </button>
          </div>
        )}
        </>
        )}
      </div>
    </div>
  );
}
