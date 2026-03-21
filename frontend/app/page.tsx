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
import DOMPurify from "dompurify";
import LoadingBar from "@/app/loading-bar";

function PriorityBadge({ priority }: { priority: string }) {
  const colors =
    priority === "high"
      ? "bg-red-50 text-red-600 border border-red-200"
      : priority === "low"
      ? "bg-slate-50 text-slate-500 border border-slate-200"
      : "bg-amber-50 text-amber-600 border border-amber-200";
  return (
    <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium ${colors}`}>
      {priority}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
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
      ? "bg-slate-100 text-slate-500 border border-slate-200"
      : "bg-slate-50 text-slate-600 border border-slate-200";
  return (
    <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium ${colors}`}>
      {status}
    </span>
  );
}

function SimpleMarkdown({ text }: { text: string }) {
  const html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-blue-600 underline hover:text-blue-800">$1</a>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/\n/g, "<br>");
  return <div className="prose text-sm text-slate-700" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />;
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
  return (
    <div className="space-y-6">
      {/* Historical banner */}
      {isHistorical && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5 flex items-center gap-2">
          <div className="w-2 h-2 bg-amber-400 rounded-full" />
          <span className="text-xs text-amber-700">
            Viewing report from <strong>{report.date}</strong> (not the latest)
          </span>
        </div>
      )}

      {/* Opportunities */}
      <div>
        <h2 className="text-sm font-semibold text-slate-800 mb-3 font-[family-name:var(--font-dm-sans)]">Opportunities</h2>
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
              className={`bg-white border border-slate-200 rounded-xl p-4 shadow-sm hover:shadow-md transition-shadow${opp.expired ? " opacity-50" : ""}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <PriorityBadge priority={opp.priority} />
                    <span className="text-sm font-medium text-slate-800 font-[family-name:var(--font-dm-sans)]">
                      {opp.title}
                    </span>
                  </div>
                  {opp.description && (
                    <p className="text-xs text-slate-600 mt-1">{opp.description}</p>
                  )}
                  <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-400">
                    {opp.deadline && <span>Deadline: {opp.deadline}</span>}
                    {opp.amount && <span>Amount: {opp.amount}</span>}
                    {opp.funder && <span>Funder: {opp.funder}</span>}
                  </div>
                  {opp.details.length > 0 && (
                    <ul className="mt-2 space-y-0.5">
                      {opp.details.map((d, i) => (
                        <li key={i} className="text-xs text-slate-500">
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
                          className="text-xs text-blue-600 underline hover:text-blue-800"
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
                      <span className="shrink-0 px-3 py-1.5 text-xs bg-slate-100 text-slate-400 rounded-lg">
                        Expired
                      </span>
                    );
                  }
                  if (existingCase) {
                    return (
                      <a
                        href={`/case/${existingCase.case_id}`}
                        className="shrink-0 px-3 py-1.5 text-xs bg-slate-100 text-slate-600 rounded-lg hover:bg-slate-200 transition-colors"
                      >
                        View Case
                      </a>
                    );
                  }
                  return (
                    <button
                      onClick={() => onOpenCase(opp)}
                      disabled={openingCase === opp.id}
                      className="shrink-0 px-4 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
                    >
                      {openingCase === opp.id ? "Creating..." : "Open Case"}
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

      {/* Donor Intelligence */}
      {report.sections["Donor Intelligence"] && (
        <div className="card-gradient border border-slate-200 rounded-xl p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-800 mb-3">Donor Intelligence</h2>
          <SimpleMarkdown text={report.sections["Donor Intelligence"]} />
        </div>
      )}

      {/* Pipeline + Gaps */}
      <div className="grid grid-cols-2 gap-4">
        {report.sections["Pipeline Update"] && (
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-800 mb-3">Pipeline</h2>
            <SimpleMarkdown text={report.sections["Pipeline Update"]} />
          </div>
        )}
        {report.sections["Gaps & Recommendations"] && (
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-800 mb-3">Gaps</h2>
            <SimpleMarkdown text={report.sections["Gaps & Recommendations"]} />
          </div>
        )}
      </div>
    </div>
  );
}

const CASE_TABS = [
  { key: "open", label: "Open", color: "text-blue-600 border-blue-600" },
  { key: "submitted", label: "Submitted", color: "text-amber-600 border-amber-600" },
  { key: "accepted", label: "Accepted", color: "text-green-600 border-green-600" },
  { key: "rejected", label: "Rejected", color: "text-red-600 border-red-600" },
  { key: "closed", label: "Closed", color: "text-slate-500 border-slate-500" },
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
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              tab === t.key
                ? t.color
                : "text-slate-400 border-transparent hover:text-slate-600"
            }`}
          >
            {t.label} ({counts[t.key]})
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-slate-400 py-4">
          {cases.length === 0
            ? "No cases yet. Open one from the Daily Brief."
            : `No ${tab} cases.`}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((c) => (
            <a
              key={c.case_id}
              href={`/case/${c.case_id}`}
              className="block p-4 rounded-xl bg-white border border-slate-200 shadow-sm transition-all hover:shadow-md"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-mono font-medium text-slate-700">
                    {c.case_id}
                  </span>
                  <span className="mx-2 text-slate-300">|</span>
                  <span className="text-sm text-slate-600">{c.grant_id}</span>
                </div>
                <StatusBadge status={c.status} />
              </div>
              <div className="mt-1 flex gap-4 text-xs text-slate-400">
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

export default function Dashboard() {
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

  const watcherMessages = [
    "Scanning",
    "Checking",
    "Crawling",
    "Indexing",
    "Parsing",
    "Matching",
    "Reviewing",
    "Pulling",
    "Screening",
    "Cross-referencing",
  ];

  useEffect(() => {
    if (cyclePhase !== "watcher") return;
    setWatcherMsgIndex(0);
    const interval = setInterval(() => {
      setWatcherMsgIndex((i) => (i + 1) % watcherMessages.length);
    }, 3500);
    return () => clearInterval(interval);
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
            `Cycle complete! Found ${status.opportunities || 0} opportunities.`
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
          setCycleMessage("Cycle failed. Check the server logs.");
        }
      } catch {
        clearInterval(poll);
        setCycleStatus("error");
        setCycleMessage("Lost connection to server.");
      }
    }, 5000);

    const timeout = setTimeout(() => {
      clearInterval(poll);
      setCycleStatus("error");
      setCycleMessage("Cycle timed out after 15 minutes.");
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
      alert(`Failed to create case: ${err instanceof Error ? err.message : "Unknown error"}`);
      setOpeningCase(null);
    }
  };

  const handleViewReport = async (date: string) => {
    try {
      const r = await getReport(date);
      setReport(r);
      setActiveView("brief");
    } catch (err) {
      alert(`Failed to load report: ${err instanceof Error ? err.message : "Unknown error"}`);
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
      const msg = err instanceof Error ? err.message : "Unknown error";
      if (msg.includes("403")) {
        setTriggerError("Invalid password.");
      } else if (msg.includes("409")) {
        setTriggerError("Cycle already active.");
      } else {
        setTriggerError(msg);
      }
      return;
    }

    // Step 2: Run the actual cycle
    try {
      await runCycle();
      setCycleMessage("Cycle started. Scanning for grant opportunities...");
      startCyclePolling();
    } catch (err) {
      setCycleStatus("error");
      const msg = err instanceof Error ? err.message : "Unknown error";
      setCycleMessage(`Failed to start cycle: ${msg}`);
    }
  };

  const handleRunCycle = async () => {
    setCycleStatus("starting");
    setCycleMessage("");
    try {
      await runCycle();
      setCycleMessage("Cycle started. Scanning for grant opportunities...");
      startCyclePolling();
    } catch (err) {
      setCycleStatus("error");
      const msg = err instanceof Error ? err.message : "Unknown error";
      setCycleMessage(`Failed to start cycle: ${msg}`);
    }
  };

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-8">
        <LoadingBar label="Loading dashboard..." />
      </div>
    );
  }

  const isHistorical = report != null && latestDate != null && report.date !== latestDate;

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Nav tabs */}
      <div className="flex items-center gap-4 mb-6 border-b border-slate-200 pb-3">
        <button
          onClick={handleViewLatest}
          className={`text-sm font-medium pb-1 transition-colors ${
            activeView === "brief"
              ? "text-slate-900 border-b-2 border-slate-900"
              : "text-slate-400 hover:text-slate-600"
          }`}
        >
          Daily Brief
        </button>
        <button
          onClick={() => setActiveView("cases")}
          className={`text-sm font-medium pb-1 transition-colors ${
            activeView === "cases"
              ? "text-slate-900 border-b-2 border-slate-900"
              : "text-slate-400 hover:text-slate-600"
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
        <button
          onClick={() => setActiveView("cycle")}
          className={`text-sm font-medium pb-1 transition-colors ${
            activeView === "cycle"
              ? "text-slate-900 border-b-2 border-slate-900"
              : "text-slate-400 hover:text-slate-600"
          }`}
        >
          Run Cycle
        </button>
        {report && (
          <span className="ml-auto text-xs text-slate-400">
            {isHistorical ? `Viewing: ${report.date}` : `Last scan: ${report.date}`}
            {" "}&middot; {report.evidence_count} evidence items
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
              <p className="text-sm text-slate-400">No reports yet. Run a cycle first.</p>
              <button
                onClick={() => setActiveView("cycle")}
                className="mt-4 px-5 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
              >
                Go to Run Cycle
              </button>
            </div>
          ) : (
            <>
              {isHistorical && (
                <div className="mb-4 flex justify-end">
                  <button
                    onClick={handleViewLatest}
                    className="text-xs px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors"
                  >
                    Back to latest ({latestDate})
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
          <div className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-800 mb-1">Run Grant Scanning Cycle</h2>
            <p className="text-xs text-slate-500 mb-4">
              Scans for opportunities, assesses fit, and compiles your daily brief. Takes around 3 minutes.
            </p>

            {cycleStatus === "idle" || cycleStatus === "error" ? (
              <div className="space-y-3">
                {/* Password-gated cycle trigger */}
                {(!session?.cycle_timer || session.cycle_timer.expired) ? (
                  <>
                    <div>
                      <label className="block text-xs text-slate-500 mb-1">Enter password to start cycle</label>
                      <input
                        type="password"
                        value={cyclePassword}
                        onChange={(e) => { setCyclePassword(e.target.value); setTriggerError(""); }}
                        onKeyDown={(e) => e.key === "Enter" && cyclePassword && handleTriggerAndRunCycle()}
                        placeholder="Your account password"
                        className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-blue-400"
                      />
                    </div>
                    {triggerError && (
                      <p className="text-xs text-red-600">{triggerError}</p>
                    )}
                    <button
                      onClick={handleTriggerAndRunCycle}
                      disabled={!cyclePassword}
                      className="w-full px-4 py-2.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
                    >
                      Run Cycle
                    </button>
                  </>
                ) : (
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                        <span className="text-xs font-medium text-blue-700">Next cycle available in</span>
                      </div>
                      {session?.cycle_timer && (
                        <span className="text-sm font-mono font-medium text-blue-600 tabular-nums">
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
                <LoadingBar label="Starting cycle..." />
              </div>
            ) : cycleStatus === "running" ? (
              <div className="py-4">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-3 h-3 rounded-full animate-pulse brand-gradient" />
                  <span className="text-sm font-medium text-slate-700">Cycle running</span>
                </div>

                {/* Progress bar */}
                {(() => {
                  const phases = [
                    { key: "watcher", label: "Watcher", desc: watcherMessages[watcherMsgIndex] },
                    { key: "analyst", label: "Analyst", desc: "Assessing fit" },
                    { key: "reporter", label: "Reporter", desc: "Compiling brief" },
                    { key: "saving", label: "Saving", desc: "Saving report" },
                  ];
                  const currentIndex = phases.findIndex((p) => p.key === cyclePhase);
                  const progress = currentIndex === -1 ? 5 : Math.min(((currentIndex + 0.5) / phases.length) * 100, 100);

                  return (
                    <div>
                      {/* Gradient progress bar */}
                      <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden mb-4">
                        <div
                          className="h-full rounded-full transition-all duration-1000 ease-out brand-gradient"
                          style={{ width: `${progress}%` }}
                        />
                      </div>

                      {/* Phase steps */}
                      <div className="space-y-2">
                        {phases.map((phase, i) => {
                          const isDone = currentIndex > i;
                          const isActive = currentIndex === i;
                          return (
                            <div key={phase.key} className="flex items-center gap-2.5">
                              {isDone ? (
                                <span className="text-green-500 text-sm w-4 text-center">&#10003;</span>
                              ) : isActive ? (
                                <div className="w-4 flex justify-center">
                                  <div className="w-2 h-2 rounded-full animate-pulse brand-gradient" />
                                </div>
                              ) : (
                                <div className="w-4 flex justify-center">
                                  <div className="w-2 h-2 bg-slate-200 rounded-full" />
                                </div>
                              )}
                              <span className={`text-xs ${
                                isActive ? "text-slate-700 font-medium" :
                                isDone ? "text-slate-400" :
                                "text-slate-400"
                              }`}>
                                {phase.label}{isActive ? ` — ${phase.desc}...` : isDone ? ` — done` : ""}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}

                <p className="text-xs text-slate-400 mt-4">
                  Takes around 3 minutes. You can switch to other tabs while it runs.
                </p>
              </div>
            ) : cycleStatus === "complete" ? (
              <div className="py-4">
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-3 h-3 bg-green-500 rounded-full" />
                  <span className="text-sm font-medium text-green-700">Cycle complete</span>
                </div>
                <button
                  onClick={() => {
                    handleViewLatest();
                    setCycleStatus("idle");
                  }}
                  className="w-full px-4 py-2.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors mt-2"
                >
                  View Daily Brief
                </button>
              </div>
            ) : null}

            {cycleMessage && (
              <p className={`text-xs mt-3 ${
                cycleStatus === "error" ? "text-red-600" :
                cycleStatus === "complete" ? "text-green-600" :
                "text-slate-500"
              }`}>
                {cycleMessage}
              </p>
            )}
          </div>

          {/* Cycle History */}
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
            <h3 className="text-xs font-semibold text-slate-700 mb-3">Cycle History</h3>
            {reportDates.length === 0 ? (
              <p className="text-xs text-slate-400">No cycles have been run yet.</p>
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
                      <span className="text-sm font-mono text-slate-700">{date}</span>
                      {date === latestDate && (
                        <span className="text-xs text-green-600 font-medium">latest</span>
                      )}
                    </div>
                    <span className="text-xs text-slate-400 group-hover:text-slate-600 transition-colors">
                      View report
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
