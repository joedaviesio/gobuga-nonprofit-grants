"use client";

import { useState } from "react";
import type { CaseSummary } from "@/lib/api";
import { useI18n, type MessageKey } from "@/lib/i18n";

// Backend statuses are English slugs; map them to translatable labels.
const STATUS_KEYS: Record<string, MessageKey> = {
  open: "cases.status_open",
  submitted: "cases.status_submitted",
  accepted: "cases.status_accepted",
  approved: "cases.status_accepted",
  rejected: "cases.status_rejected",
  closed: "cases.status_closed",
};

export function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
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
      {STATUS_KEYS[status] ? t(STATUS_KEYS[status]) : status}
    </span>
  );
}

export const CASE_TABS = [
  { key: "open", labelKey: "cases.status_open", color: "text-blue-600 border-blue-600" },
  { key: "submitted", labelKey: "cases.status_submitted", color: "text-amber-600 border-amber-600" },
  { key: "accepted", labelKey: "cases.status_accepted", color: "text-green-600 border-green-600" },
  { key: "rejected", labelKey: "cases.status_rejected", color: "text-red-600 border-red-600" },
  { key: "closed", labelKey: "cases.status_closed", color: "text-slate-700 border-slate-500" },
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
  const { t } = useI18n();
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
        {CASE_TABS.map((tabDef) => (
          <button
            key={tabDef.key}
            onClick={() => setTab(tabDef.key)}
            className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === tabDef.key
                ? tabDef.color
                : "text-slate-600 border-transparent hover:text-slate-600"
            }`}
          >
            {t(tabDef.labelKey)} ({counts[tabDef.key]})
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <p className="text-base text-slate-600 py-4">
          {cases.length === 0
            ? t("cases.none_yet")
            : t("cases.none_in_tab", { tab: t(STATUS_KEYS[tab]) })}
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
                <span>{t("cases.n_sections", { n: c.sections_count })}</span>
                <span>{t("cases.n_uploads", { n: c.uploads_count })}</span>
                <span>{t("cases.updated", { date: new Date(c.updated).toLocaleDateString() })}</span>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
