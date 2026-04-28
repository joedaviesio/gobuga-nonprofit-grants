"use client";

import { useEffect, useState } from "react";
import {
  listPublicOpportunities,
  getPublicStats,
  type PublicOpportunity,
  type PublicStats,
} from "@/lib/api";

// Anon-side sector chip set. Labels are user-facing; slugs go to
// /api/public/opportunities?tags=<slug>. Mirrors api.taxonomy.SECTOR_LABEL_TO_TAG
// for the labels we want to surface in the chip row — kept short to fit one row.
const SECTOR_CHIPS: { label: string; slug: string }[] = [
  { label: "Community", slug: "community" },
  { label: "Climate", slug: "climate" },
  { label: "Environment", slug: "environment" },
  { label: "Education", slug: "education" },
  { label: "Health", slug: "health" },
  { label: "Youth", slug: "youth" },
  { label: "Arts", slug: "arts" },
  { label: "Sport", slug: "sport" },
  { label: "Māori", slug: "maori" },
  { label: "Pasifika", slug: "pasifika" },
  { label: "Research", slug: "research" },
  { label: "Disability", slug: "disability" },
];

const PAGE_LIMIT = 20;

export default function AnonLanding() {
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [opps, setOpps] = useState<PublicOpportunity[]>([]);
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [, setFilterChanges] = useState(0);
  const [showFilterNudge, setShowFilterNudge] = useState(false);
  const [nudgeDismissed, setNudgeDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      listPublicOpportunities({ tags: activeTags, sort: "random", limit: PAGE_LIMIT }).catch(() => null),
      stats ? Promise.resolve(stats) : getPublicStats().catch(() => null),
    ]).then(([feed, s]) => {
      if (cancelled) return;
      setOpps(feed?.opportunities ?? []);
      if (s) setStats(s);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
    // stats only fetched on mount; intentionally not in deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTags]);

  const toggleTag = (slug: string) => {
    setActiveTags((prev) =>
      prev.includes(slug) ? prev.filter((t) => t !== slug) : [...prev, slug]
    );
    setFilterChanges((n) => {
      const next = n + 1;
      if (next >= 3 && !nudgeDismissed) setShowFilterNudge(true);
      return next;
    });
  };

  return (
    <div className="min-h-screen app-bg">
      <div className="max-w-5xl mx-auto px-6 py-10">
        <Hero stats={stats} />
        <SectorChips active={activeTags} onToggle={toggleTag} />

        <div className="mt-6">
          {loading ? (
            <div className="space-y-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="card-gradient border border-slate-200 p-4 animate-pulse h-24" />
              ))}
            </div>
          ) : opps.length === 0 ? (
            <p className="text-sm text-slate-600 py-8 text-center">
              No live opportunities match those filters right now.
            </p>
          ) : (
            <div className="space-y-3">
              {opps.map((opp, i) => (
                <div key={opp.id ?? i}>
                  <AnonOpportunityCard opp={opp} />
                  {(i + 1) % 5 === 0 && i < opps.length - 1 && <InlineCTA />}
                </div>
              ))}
            </div>
          )}
        </div>

        <FooterCTA />
      </div>

      {showFilterNudge && !nudgeDismissed && (
        <FilterNudge onDismiss={() => { setShowFilterNudge(false); setNudgeDismissed(true); }} />
      )}
    </div>
  );
}

function Hero({ stats }: { stats: PublicStats | null }) {
  return (
    <div className="text-center mb-8">
      <h1 className="text-2xl font-bold text-slate-900 font-[family-name:var(--font-dm-sans)]">
        Live grants for New Zealand nonprofits
      </h1>
      <p className="mt-2 text-sm text-slate-700">
        Browse what&apos;s open this week. Funders and deadlines are unlocked when you sign up — free.
      </p>
      {stats && (
        <p className="mt-2 text-xs text-slate-600">
          <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-1.5 animate-pulse" />
          <strong className="font-medium tabular-nums">{stats.live_count}</strong> live opportunities indexed
        </p>
      )}
      <div className="mt-5 flex items-center justify-center gap-3">
        <a
          href="/register"
          className="px-5 py-2 text-sm font-medium btn-gradient rounded-lg"
        >
          Sign up free
        </a>
        <a
          href="/login"
          className="px-5 py-2 text-sm font-medium text-slate-800 bg-white border border-slate-200 rounded-lg hover:border-slate-300 transition-colors"
        >
          Sign in
        </a>
      </div>
    </div>
  );
}

function SectorChips({
  active,
  onToggle,
}: {
  active: string[];
  onToggle: (slug: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 justify-center">
      {SECTOR_CHIPS.map((c) => {
        const on = active.includes(c.slug);
        return (
          <button
            key={c.slug}
            onClick={() => onToggle(c.slug)}
            className={`px-3 py-1.5 text-xs rounded-full border transition-colors ${
              on
                ? "bg-blue-50 border-blue-300 text-blue-700 shadow-sm"
                : "bg-white border-slate-200 text-slate-700 hover:border-blue-300"
            }`}
          >
            {c.label}
          </button>
        );
      })}
    </div>
  );
}

function AnonOpportunityCard({ opp }: { opp: PublicOpportunity }) {
  return (
    <div className="card-gradient border border-slate-200 p-4 shadow-sm hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-sm font-medium text-slate-800 font-[family-name:var(--font-dm-sans)]">
              {opp.title}
            </span>
            <DeadlineBadge bucket={opp.deadline_bucket} />
          </div>
          {opp.summary_preview && (
            <p className="text-xs text-slate-600 mt-1 line-clamp-2">{opp.summary_preview}</p>
          )}
          {opp.amount_band && (
            <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-600">
              <span><span className="text-slate-500">Amount:</span> {opp.amount_band}</span>
            </div>
          )}
          {opp.tags && opp.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {opp.tags.slice(0, 4).map((t) => (
                <span key={t} className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded-full">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
        <a
          href={opp.id ? `/register?from=${encodeURIComponent(opp.id)}` : "/register"}
          className="shrink-0 px-3 py-1.5 text-xs btn-gradient rounded-lg whitespace-nowrap"
        >
          Sign up to open case
        </a>
      </div>
    </div>
  );
}

function DeadlineBadge({ bucket }: { bucket: string }) {
  const tone =
    bucket === "Closes this week"
      ? "bg-red-50 text-red-600 border-red-200"
      : bucket === "Closes this month"
      ? "bg-amber-50 text-amber-700 border-amber-200"
      : bucket === "Rolling"
      ? "bg-green-50 text-green-700 border-green-200"
      : "bg-slate-50 text-slate-600 border-slate-200";
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border ${tone}`}>
      {bucket}
    </span>
  );
}

function InlineCTA() {
  return (
    <div className="my-2 px-4 py-3 rounded-xl bg-blue-50/50 border border-blue-100 text-center">
      <p className="text-xs text-blue-800">
        Funder names and exact deadlines are unlocked on a free account.{" "}
        <a href="/register" className="font-semibold underline hover:no-underline">Sign up</a>
      </p>
    </div>
  );
}

function FooterCTA() {
  return (
    <div className="mt-10 px-6 py-8 rounded-2xl card-gradient border border-slate-200 text-center">
      <h2 className="text-lg font-semibold text-slate-900 font-[family-name:var(--font-dm-sans)]">
        See the full list, with funders and deadlines
      </h2>
      <p className="mt-1 text-sm text-slate-700">
        Free Grant Scanner account
      </p>
      <a
        href="/register"
        className="inline-block mt-4 px-6 py-2.5 text-sm font-medium btn-gradient rounded-lg"
      >
        Create your free account
      </a>
    </div>
  );
}

function FilterNudge({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 max-w-md w-[calc(100%-2rem)] bg-white border border-slate-200 rounded-xl shadow-lg p-4 flex items-start gap-3">
      <div className="flex-1">
        <p className="text-sm font-medium text-slate-900">Save these filters?</p>
        <p className="text-xs text-slate-700 mt-0.5">
          Sign up free to keep your sector picks — and unlock the full opportunity feed.
        </p>
        <a
          href="/register"
          className="inline-block mt-2 px-3 py-1.5 text-xs font-medium btn-gradient rounded-lg"
        >
          Sign up free
        </a>
      </div>
      <button
        onClick={onDismiss}
        className="shrink-0 text-slate-400 hover:text-slate-600 transition-colors"
        aria-label="Dismiss"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}
