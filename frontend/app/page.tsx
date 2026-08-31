"use client";

import { useEffect, useState } from "react";
import {
  getLatestReport,
  getReport,
  listReportDates,
  listCases,
  openCaseFromOpportunity,
  runCycle,
  getCycleStatus,
  triggerCycle,
  type Report,
  type Opportunity,
  type CaseSummary,
} from "@/lib/api";
import { useAuth } from "./auth-gate";
import LoadingBar from "@/app/loading-bar";
import ErrorModal from "@/app/error-modal";
import OpportunitiesView from "./opportunities-view";
import AnonLanding from "./anon-landing";
import { useI18n, type MessageKey } from "@/lib/i18n";

const COUNTRY_SWEEP_ENABLED = process.env.NEXT_PUBLIC_COUNTRY_SWEEP_ENABLED === "true";

const PRIORITY_KEYS: Record<string, MessageKey> = {
  high: "tailored.priority_high",
  medium: "tailored.priority_medium",
  low: "tailored.priority_low",
};

const STATUS_KEYS: Record<string, MessageKey> = {
  open: "cases.status_open",
  submitted: "cases.status_submitted",
  accepted: "cases.status_accepted",
  approved: "cases.status_accepted",
  rejected: "cases.status_rejected",
  closed: "cases.status_closed",
};

function PriorityBadge({ priority }: { priority: string }) {
  const { t } = useI18n();
  const colors =
    priority === "high"
      ? "bg-red-50 text-red-600 border border-red-200"
      : priority === "low"
      ? "bg-slate-50 text-slate-700 border border-slate-200"
      : "bg-amber-50 text-amber-600 border border-amber-200";
  return (
    <span className={`text-sm px-2.5 py-0.5 rounded-full font-medium ${colors}`}>
      {PRIORITY_KEYS[priority] ? t(PRIORITY_KEYS[priority]) : priority}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
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

// --- Report View Component ---
function ReportView({
  report,
  isHistorical,
  onOpenCase,
  openingCase,
  cases,
}: {
  report: Report;
  isHistorical?: boolean;
  onOpenCase: (opp: Opportunity) => void;
  openingCase: string | null;
  cases: CaseSummary[];
}) {
  const { t } = useI18n();
  return (
    <div className="space-y-6">
      {/* Historical banner */}
      {isHistorical && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5 flex items-center gap-2">
          <div className="w-2 h-2 bg-amber-400 rounded-full" />
          <span className="text-sm text-amber-700">
            {t("dashboard.viewing_report_from")} <strong>{report.date}</strong> {t("dashboard.not_latest")}
          </span>
        </div>
      )}

      {/* Opportunities */}
      <div>
        <h2 className="text-lg font-semibold text-slate-800 mb-3 font-[family-name:var(--font-dm-sans)]">{t("opps.tab_opportunities")}</h2>
        {(() => {
          const filtered = report.opportunities.filter((opp) => {
            if (opp.expired) return false;
            const oppTitle = opp.title.toLowerCase();
            const oppWords = oppTitle.split(/\s+/).slice(0, 5);
            const matchedCase = cases.find((c) => {
              const grantWords = c.grant_id.replace(/-/g, " ").toLowerCase().split(/\s+/);
              const overlap = oppWords.filter((w) => grantWords.includes(w));
              return overlap.length >= 2;
            });
            return !(matchedCase && (matchedCase.status === "open" || matchedCase.status === "closed"));
          });
          const priorityOrder = ["high", "medium", "low"] as const;
          const sorted = priorityOrder.flatMap((p) => filtered.filter((o) => o.priority === p));
          return (
            <div className="space-y-3">
              {sorted.map((opp) => (
            <div
              key={opp.id}
              className={`card-gradient border border-slate-200 p-4 shadow-sm hover:shadow-md transition-shadow${opp.expired ? " opacity-50" : ""}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <PriorityBadge priority={opp.priority} />
                    <span className="text-base font-medium text-slate-800 font-[family-name:var(--font-dm-sans)]">
                      {opp.title}
                    </span>
                  </div>
                  {opp.description && (
                    <p className="text-sm text-slate-600 mt-1">{opp.description}</p>
                  )}
                  <div className="flex flex-wrap gap-3 mt-2 text-sm text-slate-600">
                    {opp.deadline && <span>{t("common.deadline")}: {opp.deadline}</span>}
                    {opp.amount && <span>{t("common.amount")}: {opp.amount}</span>}
                    {opp.funder && <span>{t("common.funder")}: {opp.funder}</span>}
                  </div>
                  {opp.details.length > 0 && (
                    <ul className="mt-2 space-y-0.5">
                      {opp.details.map((d, i) => (
                        <li key={i} className="text-sm text-slate-700">
                          - {d}
                        </li>
                      ))}
                    </ul>
                  )}
                  {opp.source_urls && opp.source_urls.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-2">
                      {opp.source_urls.map((link, li) => (
                        <a
                          key={li}
                          href={link.url}
                          target="_blank"
                          rel="noopener"
                          className="text-sm text-blue-600 underline hover:text-blue-800"
                        >
                          {link.title ? link.title.slice(0, 40) + (link.title.length > 40 ? "..." : "") : new URL(link.url).hostname}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
                {(() => {
                  const oppTitle = opp.title.toLowerCase();
                  const oppWords = oppTitle.split(/\s+/).slice(0, 5);
                  const existingCase = cases.find((c) => {
                    const grantWords = c.grant_id.replace(/-/g, " ").toLowerCase().split(/\s+/);
                    const overlap = oppWords.filter((w) => grantWords.includes(w));
                    return overlap.length >= 2;
                  });
                  if (opp.expired) {
                    return (
                      <span className="shrink-0 px-3 py-1.5 text-sm bg-slate-100 text-slate-600 rounded-lg">
                        {t("tailored.expired")}
                      </span>
                    );
                  }
                  if (existingCase) {
                    return (
                      <a
                        href={`/case/${existingCase.case_id}`}
                        className="shrink-0 px-3 py-1.5 text-sm bg-slate-100 text-slate-600 rounded-lg hover:bg-slate-200 transition-colors"
                      >
                        {t("opps.view_case")}
                      </a>
                    );
                  }
                  return (
                    <button
                      onClick={() => onOpenCase(opp)}
                      disabled={openingCase === opp.id}
                      className="shrink-0 px-4 py-1.5 text-sm btn-gradient rounded-lg disabled:opacity-50 transition-colors"
                    >
                      {openingCase === opp.id ? t("tailored.creating") : t("opps.open_case")}
                    </button>
                  );
                })()}
              </div>
            </div>
          ))}
            </div>
          );
        })()}
      </div>

    </div>
  );
}

const CASE_TABS = [
  { key: "open", labelKey: "cases.status_open", color: "text-blue-600 border-blue-600" },
  { key: "submitted", labelKey: "cases.status_submitted", color: "text-amber-600 border-amber-600" },
  { key: "accepted", labelKey: "cases.status_accepted", color: "text-green-600 border-green-600" },
  { key: "rejected", labelKey: "cases.status_rejected", color: "text-red-600 border-red-600" },
  { key: "closed", labelKey: "cases.status_closed", color: "text-slate-700 border-slate-500" },
] as const;

type CaseTab = (typeof CASE_TABS)[number]["key"];

function getViewedCases(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem("gobuga_viewed_cases") || "{}");
  } catch {
    return {};
  }
}

function isNewOpenCase(c: CaseSummary): boolean {
  if (c.status !== "open") return false;
  const viewed = getViewedCases();
  const lastSeen = viewed[c.case_id];
  if (!lastSeen) {
    // Auto-dismiss cases older than 48h
    const age = Date.now() - new Date(c.created).getTime();
    if (age > 48 * 60 * 60 * 1000) return false;
    return true;
  }
  return false;
}

function CasesView({ cases }: { cases: CaseSummary[] }) {
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
  for (const tabDef of CASE_TABS) {
    counts[tabDef.key] = cases.filter((c) => {
      if (tabDef.key === "accepted") return c.status === "accepted" || c.status === "approved";
      return c.status === tabDef.key;
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

export default function Dashboard() {
  const { session, loading } = useAuth();
  const { t } = useI18n();
  if (loading) {
    return (
      <div className="min-h-screen app-bg">
        <div className="max-w-5xl mx-auto px-6 py-8">
          <LoadingBar label={t("common.loading")} />
        </div>
      </div>
    );
  }
  if (!session) {
    return <AnonLanding />;
  }
  if (COUNTRY_SWEEP_ENABLED) {
    return <OpportunitiesView />;
  }
  return <LegacyDashboard />;
}

function LegacyDashboard() {
  const { t } = useI18n();
  const { session, refreshSession } = useAuth();
  const [report, setReport] = useState<Report | null>(null);
  const [latestDate, setLatestDate] = useState<string | null>(null);
  const [reportDates, setReportDates] = useState<string[]>([]);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [openingCase, setOpeningCase] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"brief" | "cases" | "cycle">("brief");
  const [cycleStatus, setCycleStatus] = useState<string>("idle");
  const [cyclePhase, setCyclePhase] = useState<string | null>(null);
  const [watcherMsgIndex, setWatcherMsgIndex] = useState(0);
  const [cyclePassword, setCyclePassword] = useState("");
  const [triggerError, setTriggerError] = useState("");
  const [errorModal, setErrorModal] = useState<string | null>(null);

  const phaseMessages: Record<string, string[]> = {
    watcher: t("tailored.msgs_watcher").split("|"),
    analyst: t("tailored.msgs_analyst").split("|"),
    reporter: t("tailored.msgs_reporter").split("|"),
    saving: t("tailored.msgs_saving").split("|"),
  };

  const [phaseMessageKey, setPhaseMessageKey] = useState(0);
  const [itemsFound, setItemsFound] = useState(0);
  const [prevPhase, setPrevPhase] = useState<string | null>(null);
  const [phaseJustCompleted, setPhaseJustCompleted] = useState<string | null>(null);

  useEffect(() => {
    if (cycleStatus !== "running") return;
    setPhaseMessageKey(0);
    const interval = setInterval(() => {
      setPhaseMessageKey((i) => i + 1);
    }, 2000);
    return () => clearInterval(interval);
  }, [cyclePhase, cycleStatus]);

  // Simulate activity counter ticking up during the cycle
  useEffect(() => {
    if (cycleStatus !== "running") { setItemsFound(0); return; }
    const interval = setInterval(() => {
      setItemsFound((n) => {
        if (cyclePhase === "watcher") return n + Math.ceil(Math.random() * 3);
        if (cyclePhase === "analyst") return n + (Math.random() > 0.6 ? 1 : 0);
        return n;
      });
    }, 800);
    return () => clearInterval(interval);
  }, [cycleStatus, cyclePhase]);

  // Track phase transitions for flash animation
  useEffect(() => {
    if (cyclePhase && prevPhase && cyclePhase !== prevPhase) {
      setPhaseJustCompleted(prevPhase);
      const t = setTimeout(() => setPhaseJustCompleted(null), 800);
      return () => clearTimeout(t);
    }
    setPrevPhase(cyclePhase);
  }, [cyclePhase]);

  const [cycleMessage, setCycleMessage] = useState("");

  const startCyclePolling = () => {
    setCycleStatus("running");
    const poll = setInterval(async () => {
      try {
        const status = await getCycleStatus();
        if (status.status === "running" && status.phase) {
          setCyclePhase(status.phase);
        }
        if (status.status === "complete") {
          clearInterval(poll);
          setCycleStatus("complete");
          setCyclePhase("complete");
          setCycleMessage(
            t("dashboard.cycle_complete_found", { n: status.opportunities || 0 })
          );
          const newReport = await getLatestReport().catch(() => null);
          if (newReport) {
            setReport(newReport);
            setLatestDate(newReport.date);
          }
          const newCases = await listCases();
          setCases(newCases);
          const newDates = await listReportDates().catch(() => []);
          setReportDates(newDates);
        }
        if (status.status === "error") {
          clearInterval(poll);
          setCycleStatus("error");
          setCycleMessage(t("dashboard.cycle_failed"));
        }
      } catch {
        clearInterval(poll);
        setCycleStatus("error");
        setCycleMessage(t("dashboard.lost_connection"));
      }
    }, 5000);

    const timeout = setTimeout(() => {
      clearInterval(poll);
      setCycleStatus("error");
      setCycleMessage(t("dashboard.cycle_timeout"));
    }, 900000);

    return () => {
      clearInterval(poll);
      clearTimeout(timeout);
    };
  };

  useEffect(() => {
    Promise.all([
      getLatestReport().catch(() => null),
      listCases(),
      listReportDates().catch(() => []),
      getCycleStatus().catch(() => null),
    ])
      .then(([r, c, dates, cycleCheck]) => {
        setReport(r);
        setLatestDate(r?.date || null);
        setCases(c);
        setReportDates(dates);
        if (cycleCheck && cycleCheck.status === "running") {
          if (cycleCheck.phase) setCyclePhase(cycleCheck.phase);
          startCyclePolling();
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const handleOpenCase = async (opp: Opportunity) => {
    setOpeningCase(opp.id);
    try {
      const newCase = await openCaseFromOpportunity(opp.id, opp.cycle_date);
      const updatedCases = await listCases();
      setCases(updatedCases);
      window.location.href = `/case/${newCase.case_id}`;
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : t("errors.generic"));
      setOpeningCase(null);
    }
  };

  const handleViewReport = async (date: string) => {
    try {
      const r = await getReport(date);
      setReport(r);
      setActiveView("brief");
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : t("errors.generic"));
    }
  };

  const handleViewLatest = async () => {
    try {
      const r = await getLatestReport();
      setReport(r);
      setLatestDate(r?.date || null);
      setActiveView("brief");
    } catch {
      // no report
    }
  };

  const handleTriggerAndRunCycle = async () => {
    setTriggerError("");
    setCycleStatus("starting");
    setCycleMessage("");

    // Step 1: Trigger the cycle timer with password
    try {
      await triggerCycle(cyclePassword);
      setCyclePassword("");
      await refreshSession(); // update timer in header
    } catch (err) {
      setCycleStatus("idle");
      const msg = err instanceof Error ? err.message : t("dashboard.unknown_error");
      if (msg.includes("403")) {
        setTriggerError(t("dashboard.invalid_password"));
      } else if (msg.includes("409")) {
        setTriggerError(t("dashboard.cycle_already_active"));
      } else {
        setTriggerError(msg);
      }
      return;
    }

    // Step 2: Run the actual cycle
    try {
      await runCycle();
      setCycleMessage(t("dashboard.cycle_started"));
      startCyclePolling();
    } catch (err) {
      setCycleStatus("error");
      const msg = err instanceof Error ? err.message : t("dashboard.unknown_error");
      setCycleMessage(`${t("tailored.start_failed")}: ${msg}`);
    }
  };

  const handleRunCycle = async () => {
    setCycleStatus("starting");
    setCycleMessage("");
    try {
      await runCycle();
      setCycleMessage(t("dashboard.cycle_started"));
      startCyclePolling();
    } catch (err) {
      setCycleStatus("error");
      const msg = err instanceof Error ? err.message : t("dashboard.unknown_error");
      setCycleMessage(`${t("tailored.start_failed")}: ${msg}`);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen app-bg">
        <div className="max-w-5xl mx-auto px-6 py-8">
          <LoadingBar label={t("dashboard.loading")} />
        </div>
      </div>
    );
  }

  const isHistorical = report != null && latestDate != null && report.date !== latestDate;

  return (
    <div className="min-h-screen app-bg">
    <div className="max-w-5xl mx-auto px-6 py-8">
      <ErrorModal message={errorModal} onClose={() => setErrorModal(null)} />
      {/* Nav tabs */}
      <div className="flex items-center gap-4 mb-6 border-b border-slate-200 pb-3">
        <button
          onClick={handleViewLatest}
          className={`text-base font-medium pb-1 transition-colors ${
            activeView === "brief"
              ? "text-slate-900 border-b-2 border-slate-900"
              : "text-slate-600 hover:text-slate-600"
          }`}
        >
          {t("opps.tab_opportunities")}
        </button>
        <button
          onClick={() => setActiveView("cases")}
          className={`text-base font-medium pb-1 transition-colors ${
            activeView === "cases"
              ? "text-slate-900 border-b-2 border-slate-900"
              : "text-slate-600 hover:text-slate-600"
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
        <button
          onClick={() => setActiveView("cycle")}
          className={`text-base font-medium pb-1 transition-colors ${
            activeView === "cycle"
              ? "text-slate-900 border-b-2 border-slate-900"
              : "text-slate-600 hover:text-slate-600"
          }`}
        >
          {t("dashboard.run_cycle")}
        </button>
        {report && (
          <span className="ml-auto text-sm text-slate-600">
            {isHistorical ? `${t("dashboard.viewing")}: ${report.date}` : `${t("dashboard.last_scan")}: ${report.date}`}
            {" "}&middot; {t("dashboard.n_evidence", { n: report.evidence_count })}
          </span>
        )}
      </div>

      {/* Daily Brief view */}
      {activeView === "brief" && (
        <>
          {!report ? (
            <div className="text-center py-16">
              <div className="mx-auto mb-6 w-64 h-32 rounded-xl overflow-hidden border border-slate-200 shadow-sm">
                <div className="pixel-gradient w-full h-full" />
              </div>
              <p className="text-base text-slate-600">{t("dashboard.no_reports")}</p>
              <button
                onClick={() => setActiveView("cycle")}
                className="mt-4 px-5 py-2 text-base btn-gradient rounded-lg transition-colors"
              >
                {t("dashboard.go_to_run_cycle")}
              </button>
            </div>
          ) : (
            <>
              {isHistorical && (
                <div className="mb-4 flex justify-end">
                  <button
                    onClick={handleViewLatest}
                    className="text-sm px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors"
                  >
                    {t("dashboard.back_to_latest")} ({latestDate})
                  </button>
                </div>
              )}
              <ReportView
                report={report}
                isHistorical={isHistorical}
                onOpenCase={handleOpenCase}
                openingCase={openingCase}
                cases={cases}
              />
            </>
          )}
        </>
      )}

      {/* Cases view */}
      {activeView === "cases" && (
        <CasesView cases={cases} />
      )}

      {/* Run Cycle view */}
      {activeView === "cycle" && (
        <div className="max-w-lg mx-auto space-y-6">
          {/* Run controls */}
          <div className="card-gradient border border-slate-200 p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-800 mb-1">{t("dashboard.run_cycle_title")}</h2>
            <p className="text-sm text-slate-700 mb-4">
              {t("dashboard.run_cycle_desc")}
            </p>

            {cycleStatus === "idle" || cycleStatus === "error" ? (
              <div className="space-y-3">
                {/* Password-gated cycle trigger */}
                {(!session?.cycle_timer || session.cycle_timer.expired) ? (
                  <>
                    <div>
                      <label className="block text-sm text-slate-700 mb-1">{t("dashboard.enter_password")}</label>
                      <input
                        type="password"
                        value={cyclePassword}
                        onChange={(e) => { setCyclePassword(e.target.value); setTriggerError(""); }}
                        onKeyDown={(e) => e.key === "Enter" && cyclePassword && handleTriggerAndRunCycle()}
                        placeholder={t("dashboard.account_password")}
                        className="w-full px-3 py-2 text-base border border-slate-200 rounded-lg focus:outline-none focus:border-blue-400"
                      />
                    </div>
                    {triggerError && (
                      <p className="text-sm text-red-600">{triggerError}</p>
                    )}
                    <button
                      onClick={handleTriggerAndRunCycle}
                      disabled={!cyclePassword}
                      className="w-full px-4 py-2.5 text-base btn-gradient rounded-lg disabled:opacity-50 transition-colors"
                    >
                      {t("dashboard.run_cycle")}
                    </button>
                  </>
                ) : (
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                        <span className="text-sm font-medium text-blue-700">{t("dashboard.next_cycle_in")}</span>
                      </div>
                      {session?.cycle_timer && (
                        <span className="text-base font-mono font-medium text-blue-600 tabular-nums">
                          {(() => {
                            const remaining = Math.max(0, Math.floor((new Date(session.cycle_timer.expires_at).getTime() - Date.now()) / 1000));
                            const d = Math.floor(remaining / 86400);
                            const h = Math.floor((remaining % 86400) / 3600);
                            const m = Math.floor((remaining % 3600) / 60);
                            if (d > 0) return `${d}d ${h}h ${m}m`;
                            if (h > 0) return `${h}h ${m}m`;
                            return `${m}m`;
                          })()}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ) : cycleStatus === "starting" ? (
              <div className="py-4">
                <LoadingBar label={t("dashboard.starting_cycle")} />
              </div>
            ) : cycleStatus === "running" ? (
              <div className="py-4">
                {/* Header with live dot and activity counter */}
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-3 h-3 rounded-full brand-gradient live-dot" />
                    <span className="text-base font-medium text-slate-700">{t("tailored.cycle_running")}</span>
                  </div>
                  {itemsFound > 0 && (
                    <span key={itemsFound} className="text-sm font-mono font-medium text-slate-500 tabular-nums counter-tick">
                      {t("tailored.items_scanned", { n: itemsFound })}
                    </span>
                  )}
                </div>

                {/* Progress bar with shimmer */}
                {(() => {
                  const phases = [
                    { key: "watcher", label: t("tailored.phase_watcher") },
                    { key: "analyst", label: t("tailored.phase_analyst") },
                    { key: "reporter", label: t("tailored.phase_reporter") },
                    { key: "saving", label: t("tailored.phase_saving") },
                  ];
                  const currentIndex = phases.findIndex((p) => p.key === cyclePhase);
                  const progress = currentIndex === -1 ? 3 : Math.min(((currentIndex + 0.5) / phases.length) * 100, 98);
                  const msgs = cyclePhase ? (phaseMessages[cyclePhase] || []) : [];
                  const currentMsg = msgs.length > 0 ? msgs[phaseMessageKey % msgs.length] : "";

                  return (
                    <div>
                      {/* Gradient progress bar with shimmer */}
                      <div className="w-full h-2.5 bg-slate-100 rounded-full overflow-hidden mb-1.5">
                        <div
                          className="h-full rounded-full transition-all duration-1000 ease-out brand-gradient progress-shimmer"
                          style={{ width: `${progress}%` }}
                        />
                      </div>

                      {/* Live rotating message */}
                      {currentMsg && (
                        <p key={`${cyclePhase}-${phaseMessageKey}`} className="text-[11px] text-slate-400 mb-4 cycle-msg-enter">
                          {currentMsg}...
                        </p>
                      )}

                      {/* Phase steps */}
                      <div className="space-y-1.5">
                        {phases.map((phase, i) => {
                          const isDone = currentIndex > i;
                          const isActive = currentIndex === i;
                          const justDone = phaseJustCompleted === phase.key;
                          return (
                            <div
                              key={phase.key}
                              className={`flex items-center gap-2.5 px-2 py-1 ${justDone ? "phase-just-done" : ""}`}
                            >
                              {isDone ? (
                                <span className="text-green-500 text-base w-4 text-center">&#10003;</span>
                              ) : isActive ? (
                                <div className="w-4 flex justify-center">
                                  <div className="w-2.5 h-2.5 rounded-full brand-gradient live-dot" />
                                </div>
                              ) : (
                                <div className="w-4 flex justify-center">
                                  <div className="w-2 h-2 bg-slate-200 rounded-full" />
                                </div>
                              )}
                              <span className={`text-sm transition-colors duration-300 ${
                                isActive ? "text-slate-800 font-semibold" :
                                isDone ? "text-green-600 font-medium" :
                                "text-slate-400"
                              }`}>
                                {phase.label}
                                {isDone && ` — ${t("tailored.done")}`}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}

                <p className="text-[11px] text-slate-400 mt-4">
                  {t("tailored.cycle_hint")}
                </p>
              </div>
            ) : cycleStatus === "complete" ? (
              <div className="py-4 cycle-complete-enter">
                <div className="bg-green-50 border border-green-200 rounded-lg p-4 mb-3">
                  <div className="flex items-center gap-2.5 mb-1">
                    <span className="text-green-500 text-xl">&#10003;</span>
                    <span className="text-base font-semibold text-green-800">{t("dashboard.cycle_complete")}</span>
                  </div>
                  {cycleMessage && (
                    <p className="text-sm text-green-600 ml-7">{cycleMessage}</p>
                  )}
                </div>
                <button
                  onClick={() => {
                    handleViewLatest();
                    setCycleStatus("idle");
                  }}
                  className="w-full px-4 py-2.5 text-base btn-gradient rounded-lg transition-colors"
                >
                  {t("dashboard.view_opportunities")}
                </button>
              </div>
            ) : null}

            {cycleMessage && (
              <p className={`text-sm mt-3 ${
                cycleStatus === "error" ? "text-red-600" :
                cycleStatus === "complete" ? "text-green-600" :
                "text-slate-700"
              }`}>
                {cycleMessage}
              </p>
            )}
          </div>

          {/* Cycle History */}
          <div className="card-gradient border border-slate-200 p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-700 mb-3">{t("dashboard.cycle_history")}</h3>
            {reportDates.length === 0 ? (
              <p className="text-sm text-slate-600">{t("dashboard.no_cycles")}</p>
            ) : (
              <div className="space-y-1">
                {[...reportDates].sort((a, b) => b.localeCompare(a)).map((date) => (
                  <button
                    key={date}
                    onClick={() => handleViewReport(date)}
                    className="w-full flex items-center justify-between p-2.5 rounded-lg hover:bg-slate-50 text-left group transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      {date === latestDate ? (
                        <div className="w-2 h-2 bg-green-400 rounded-full" />
                      ) : (
                        <div className="w-2 h-2 bg-slate-300 rounded-full" />
                      )}
                      <span className="text-base font-mono text-slate-700">{date}</span>
                      {date === latestDate && (
                        <span className="text-sm text-green-600 font-medium">{t("dashboard.latest")}</span>
                      )}
                    </div>
                    <span className="text-sm text-slate-600 group-hover:text-slate-600 transition-colors">
                      {t("dashboard.view_report")}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
    </div>
  );
}
