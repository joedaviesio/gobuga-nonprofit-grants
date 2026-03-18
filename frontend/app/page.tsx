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
  type Report,
  type Opportunity,
  type CaseSummary,
} from "@/lib/api";

function PriorityBadge({ priority }: { priority: string }) {
  const colors =
    priority === "high"
      ? "bg-red-100 text-red-700"
      : priority === "low"
      ? "bg-stone-100 text-stone-500"
      : "bg-amber-100 text-amber-700";
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colors}`}>
      {priority}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors =
    status === "open"
      ? "bg-blue-100 text-blue-700"
      : status === "submitted"
      ? "bg-amber-100 text-amber-700"
      : status === "accepted" || status === "approved"
      ? "bg-green-100 text-green-700"
      : status === "rejected"
      ? "bg-red-100 text-red-700"
      : status === "closed"
      ? "bg-stone-200 text-stone-500"
      : "bg-stone-100 text-stone-600";
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colors}`}>
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
  return <div className="prose text-sm" dangerouslySetInnerHTML={{ __html: html }} />;
}

// --- Report View Component (reused for brief tab and history viewing) ---
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
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-2 flex items-center gap-2">
          <div className="w-2 h-2 bg-amber-400 rounded-full" />
          <span className="text-xs text-amber-700">
            Viewing report from <strong>{report.date}</strong> (not the latest)
          </span>
        </div>
      )}

      {/* Action Required */}
      {report.sections["Action Required"] && (
        <div className="bg-white border border-stone-200 rounded-lg p-5">
          <h2 className="text-sm font-bold text-stone-800 mb-3">Action Required</h2>
          <SimpleMarkdown text={report.sections["Action Required"]} />
        </div>
      )}

      {/* Opportunities */}
      <div>
        <h2 className="text-sm font-bold text-stone-800 mb-3">Opportunities</h2>
        <div className="space-y-3">
          {report.opportunities.filter((opp) => {
            const oppTitle = opp.title.toLowerCase();
            const oppWords = oppTitle.split(/\s+/).slice(0, 5);
            const matchedCase = cases.find((c) => {
              const grantWords = c.grant_id.replace(/-/g, " ").toLowerCase().split(/\s+/);
              const overlap = oppWords.filter((w) => grantWords.includes(w));
              return overlap.length >= 2;
            });
            return !(matchedCase && (matchedCase.status === "open" || matchedCase.status === "closed"));
          }).map((opp) => (
            <div
              key={opp.id}
              className={`bg-white border border-stone-200 rounded-lg p-4${opp.expired ? " opacity-50" : ""}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <PriorityBadge priority={opp.priority} />
                    <span className="text-sm font-medium text-stone-800">
                      {opp.title}
                    </span>
                  </div>
                  {opp.description && (
                    <p className="text-xs text-stone-600 mt-1">{opp.description}</p>
                  )}
                  <div className="flex flex-wrap gap-3 mt-2 text-xs text-stone-400">
                    {opp.deadline && <span>Deadline: {opp.deadline}</span>}
                    {opp.amount && <span>Amount: {opp.amount}</span>}
                    {opp.funder && <span>Funder: {opp.funder}</span>}
                  </div>
                  {opp.details.length > 0 && (
                    <ul className="mt-2 space-y-0.5">
                      {opp.details.map((d, i) => (
                        <li key={i} className="text-xs text-stone-500">
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
                      <span className="shrink-0 px-3 py-1.5 text-xs bg-stone-100 text-stone-400 rounded-md">
                        Expired
                      </span>
                    );
                  }
                  if (existingCase) {
                    return (
                      <a
                        href={`/case/${existingCase.case_id}`}
                        className="shrink-0 px-3 py-1.5 text-xs bg-stone-100 text-stone-600 rounded-md hover:bg-stone-200"
                      >
                        View Case
                      </a>
                    );
                  }
                  return (
                    <button
                      onClick={() => onOpenCase(opp)}
                      disabled={openingCase === opp.id}
                      className="shrink-0 px-3 py-1.5 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
                    >
                      {openingCase === opp.id ? "Creating..." : "Open Case"}
                    </button>
                  );
                })()}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Donor Intelligence */}
      {report.sections["Donor Intelligence"] && (
        <div className="bg-white border border-stone-200 rounded-lg p-5">
          <h2 className="text-sm font-bold text-stone-800 mb-3">Donor Intelligence</h2>
          <SimpleMarkdown text={report.sections["Donor Intelligence"]} />
        </div>
      )}

      {/* Pipeline + Gaps */}
      <div className="grid grid-cols-2 gap-4">
        {report.sections["Pipeline Update"] && (
          <div className="bg-white border border-stone-200 rounded-lg p-5">
            <h2 className="text-sm font-bold text-stone-800 mb-3">Pipeline</h2>
            <SimpleMarkdown text={report.sections["Pipeline Update"]} />
          </div>
        )}
        {report.sections["Gaps & Recommendations"] && (
          <div className="bg-white border border-stone-200 rounded-lg p-5">
            <h2 className="text-sm font-bold text-stone-800 mb-3">Gaps</h2>
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
  { key: "closed", label: "Closed", color: "text-stone-500 border-stone-500" },
] as const;

type CaseTab = (typeof CASE_TABS)[number]["key"];

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
      <div className="flex gap-1 mb-4 border-b border-stone-200">
        {CASE_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              tab === t.key
                ? t.color
                : "text-stone-400 border-transparent hover:text-stone-600"
            }`}
          >
            {t.label} ({counts[t.key]})
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-stone-400 py-4">
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
              className="block p-4 border border-stone-200 rounded-lg bg-white hover:border-blue-300 transition-colors"
            >
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm font-mono font-medium text-stone-700">
                    {c.case_id}
                  </span>
                  <span className="mx-2 text-stone-300">|</span>
                  <span className="text-sm text-stone-600">{c.grant_id}</span>
                </div>
                <StatusBadge status={c.status} />
              </div>
              <div className="mt-1 flex gap-4 text-xs text-stone-400">
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
  const [report, setReport] = useState<Report | null>(null);
  const [latestDate, setLatestDate] = useState<string | null>(null);
  const [reportDates, setReportDates] = useState<string[]>([]);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [openingCase, setOpeningCase] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"brief" | "cases" | "cycle">("brief");
  const [cycleStatus, setCycleStatus] = useState<string>("idle");
  const [cycleMessage, setCycleMessage] = useState("");

  useEffect(() => {
    Promise.all([
      getLatestReport().catch(() => null),
      listCases(),
      listReportDates().catch(() => []),
    ])
      .then(([r, c, dates]) => {
        setReport(r);
        setLatestDate(r?.date || null);
        setCases(c);
        setReportDates(dates);
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

  const handleRunCycle = async () => {
    setCycleStatus("starting");
    setCycleMessage("");
    try {
      await runCycle();
      setCycleStatus("running");
      setCycleMessage("Cycle started. Bots are scanning for grant opportunities...");

      const poll = setInterval(async () => {
        try {
          const status = await getCycleStatus();
          if (status.status === "complete") {
            clearInterval(poll);
            setCycleStatus("complete");
            setCycleMessage(
              `Cycle complete! Found ${status.opportunities || 0} opportunities.`
            );
            // Refresh everything
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
        } catch {
          // still running
        }
      }, 10000);

      setTimeout(() => {
        clearInterval(poll);
        if (cycleStatus === "running") {
          setCycleStatus("timeout");
          setCycleMessage("Cycle is taking longer than expected. Check the server logs.");
        }
      }, 900000);
    } catch (err) {
      setCycleStatus("error");
      const msg = err instanceof Error ? err.message : "Unknown error";
      if (msg.includes("403")) {
        setCycleMessage("Invalid password.");
      } else {
        setCycleMessage(`Failed to start cycle: ${msg}`);
      }
    }
  };

  if (loading) {
    return <div className="max-w-5xl mx-auto px-6 py-8 text-sm text-stone-400">Loading...</div>;
  }

  const isHistorical = report != null && latestDate != null && report.date !== latestDate;

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Nav tabs */}
      <div className="flex items-center gap-4 mb-6 border-b border-stone-200 pb-3">
        <button
          onClick={handleViewLatest}
          className={`text-sm font-medium pb-1 ${
            activeView === "brief"
              ? "text-blue-600 border-b-2 border-blue-600"
              : "text-stone-400 hover:text-stone-600"
          }`}
        >
          Daily Brief
        </button>
        <button
          onClick={() => setActiveView("cases")}
          className={`text-sm font-medium pb-1 ${
            activeView === "cases"
              ? "text-blue-600 border-b-2 border-blue-600"
              : "text-stone-400 hover:text-stone-600"
          }`}
        >
          Cases ({cases.length})
        </button>
        <button
          onClick={() => setActiveView("cycle")}
          className={`text-sm font-medium pb-1 ${
            activeView === "cycle"
              ? "text-blue-600 border-b-2 border-blue-600"
              : "text-stone-400 hover:text-stone-600"
          }`}
        >
          Run Cycle
        </button>
        {report && (
          <span className="ml-auto text-xs text-stone-400">
            {isHistorical ? `Viewing: ${report.date}` : `Last scan: ${report.date}`}
            {" "}&middot; {report.evidence_count} evidence items
          </span>
        )}
      </div>

      {/* Daily Brief view */}
      {activeView === "brief" && (
        <>
          {!report ? (
            <div className="text-center py-12">
              <p className="text-sm text-stone-400">No reports yet. Run a cycle first.</p>
              <button
                onClick={() => setActiveView("cycle")}
                className="mt-3 px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
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
                    className="text-xs px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700"
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
          <div className="bg-white border border-stone-200 rounded-lg p-6">
            <h2 className="text-sm font-bold text-stone-800 mb-1">Run Grant Scanning Cycle</h2>
            <p className="text-xs text-stone-500 mb-4">
              Launches the 3-bot system: Watcher scans for opportunities, Analyst assesses fit, Reporter compiles the brief. Takes 5-15 minutes.
            </p>

            {cycleStatus === "idle" || cycleStatus === "error" ? (
              <div className="space-y-3">
                <button
                  onClick={handleRunCycle}
                  className="w-full px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
                >
                  Run Cycle
                </button>
              </div>
            ) : cycleStatus === "starting" ? (
              <div className="text-center py-4">
                <div className="flex justify-center gap-1 mb-2">
                  <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
                <p className="text-xs text-stone-500">Starting cycle...</p>
              </div>
            ) : cycleStatus === "running" ? (
              <div className="py-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-3 h-3 bg-blue-500 rounded-full animate-pulse" />
                  <span className="text-sm font-medium text-blue-700">Cycle running</span>
                </div>
                <div className="space-y-2 text-xs text-stone-500">
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-0.5 bg-blue-400 rounded" />
                    <span>Grant Watcher scanning web sources...</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-0.5 bg-stone-200 rounded" />
                    <span>Grant Analyst waiting for evidence...</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-0.5 bg-stone-200 rounded" />
                    <span>Grant Reporter waiting to compile brief...</span>
                  </div>
                </div>
                <p className="text-xs text-stone-400 mt-4">
                  Takes 5-15 minutes. You can switch to other tabs while it runs.
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
                  className="w-full px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 mt-2"
                >
                  View Daily Brief
                </button>
              </div>
            ) : null}

            {cycleMessage && (
              <p className={`text-xs mt-3 ${
                cycleStatus === "error" ? "text-red-600" :
                cycleStatus === "complete" ? "text-green-600" :
                "text-stone-500"
              }`}>
                {cycleMessage}
              </p>
            )}
          </div>

          {/* Cycle History */}
          <div className="bg-white border border-stone-200 rounded-lg p-5">
            <h3 className="text-xs font-bold text-stone-700 mb-3">Cycle History</h3>
            {reportDates.length === 0 ? (
              <p className="text-xs text-stone-400">No cycles have been run yet.</p>
            ) : (
              <div className="space-y-1">
                {[...reportDates].sort((a, b) => b.localeCompare(a)).map((date) => (
                  <button
                    key={date}
                    onClick={() => handleViewReport(date)}
                    className="w-full flex items-center justify-between p-2.5 rounded-md hover:bg-stone-50 text-left group"
                  >
                    <div className="flex items-center gap-2">
                      {date === latestDate ? (
                        <div className="w-2 h-2 bg-green-400 rounded-full" />
                      ) : (
                        <div className="w-2 h-2 bg-stone-300 rounded-full" />
                      )}
                      <span className="text-sm font-mono text-stone-700">{date}</span>
                      {date === latestDate && (
                        <span className="text-xs text-green-600 font-medium">latest</span>
                      )}
                    </div>
                    <span className="text-xs text-stone-400 group-hover:text-blue-600">
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
