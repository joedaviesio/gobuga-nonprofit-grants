"use client";

import { useState } from "react";
import type { CaseSummary } from "@/lib/api";

export function StatusBadge({ status }: { status: string }) {
  const colors =
    status === "open"
      ? "bg-blue-50 text-blue-600 border border-blue-200"
      : status === "submitted"
      ? "bg-amber-50 text-amber-600 border border-amber-200"
      : status === "accepted" || status === "approved"
      ? "bg-green-50 text-green-600 border border-green-200"
      : status === "rejected"
      ? "bg-red-50 text-red-600 border border-red-200"
      : status === "closed"
      ? "bg-slate-100 text-slate-700 border border-slate-200"
      : "bg-slate-50 text-slate-600 border border-slate-200";
  return (
    <span className={`text-sm px-2.5 py-0.5 rounded-full font-medium ${colors}`}>
      {status}
    </span>
  );
}

export const CASE_TABS = [
  { key: "open", label: "Open", color: "text-blue-600 border-blue-600" },
  { key: "submitted", label: "Submitted", color: "text-amber-600 border-amber-600" },
  { key: "accepted", label: "Accepted", color: "text-green-600 border-green-600" },
  { key: "rejected", label: "Rejected", color: "text-red-600 border-red-600" },
  { key: "closed", label: "Closed", color: "text-slate-700 border-slate-500" },
] as const;

type CaseTab = (typeof CASE_TABS)[number]["key"];

export function getViewedCases(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem("gobuga_viewed_cases") || "{}");
  } catch {
    return {};
  }
}

export function isNewOpenCase(c: CaseSummary): boolean {
  if (c.status !== "open") return false;
  const viewed = getViewedCases();
  const lastSeen = viewed[c.case_id];
  if (!lastSeen) {
    const age = Date.now() - new Date(c.created).getTime();
    if (age > 48 * 60 * 60 * 1000) return false;
    return true;
  }
  return false;
}

export default function CasesView({ cases }: { cases: CaseSummary[] }) {
  const [tab, setTab] = useState<CaseTab>("open");

  const filtered = cases.filter((c) => {
    if (tab === "open") return c.status === "open";
    if (tab === "submitted") return c.status === "submitted";
    if (tab === "accepted") return c.status === "accepted" || c.status === "approved";
    if (tab === "rejected") return c.status === "rejected";
    if (tab === "closed") return c.status === "closed";
    return false;
  });

  const counts: Record<string, number> = {};
  for (const t of CASE_TABS) {
    counts[t.key] = cases.filter((c) => {
      if (t.key === "accepted") return c.status === "accepted" || c.status === "approved";
      return c.status === t.key;
    }).length;
  }

  return (
    <div>
      <div className="flex gap-1 mb-4 border-b border-slate-200">
        {CASE_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? t.color
                : "text-slate-600 border-transparent hover:text-slate-600"
            }`}
          >
            {t.label} ({counts[t.key]})
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <p className="text-base text-slate-600 py-4">
          {cases.length === 0
            ? "No cases yet. Open one from Opportunities."
            : `No ${tab} cases.`}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((c) => (
            <a
              key={c.case_id}
              href={`/case/${c.case_id}`}
              className="block p-4 rounded-xl card-gradient border border-slate-200 shadow-sm transition-all hover:shadow-md"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-base font-mono font-medium text-slate-700">
                    {c.case_id}
                  </span>
                  <span className="mx-2 text-slate-300">|</span>
                  <span className="text-base text-slate-600">{c.grant_id}</span>
                </div>
                <StatusBadge status={c.status} />
              </div>
              <div className="mt-1 flex gap-4 text-sm text-slate-600">
                <span>{c.sections_count} sections</span>
                <span>{c.uploads_count} uploads</span>
                <span>updated {new Date(c.updated).toLocaleDateString()}</span>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
