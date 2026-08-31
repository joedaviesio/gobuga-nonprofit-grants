"use client";

import { useEffect, useRef, useState } from "react";
import {
  createCheckout,
  getCycleStatus,
  getLatestReport,
  getTailoredAccess,
  listCases,
  openCaseFromOpportunity,
  runTailoredCycle,
  type CaseSummary,
  type Opportunity,
  type Report,
  type TailoredAccess,
} from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import ErrorModal from "@/app/error-modal";
import { useI18n, type MessageKey } from "@/lib/i18n";

const PRIORITY_KEYS: Record<string, MessageKey> = {
  high: "tailored.priority_high",
  medium: "tailored.priority_medium",
  low: "tailored.priority_low",
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

function formatRemaining(seconds: number, readyLabel: string): string {
  if (seconds <= 0) return readyLabel;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

// Rotating messages live in the catalog as one pipe-separated string per phase.
const PHASE_MESSAGE_KEYS: Record<string, MessageKey> = {
  watcher: "tailored.msgs_watcher",
  analyst: "tailored.msgs_analyst",
  reporter: "tailored.msgs_reporter",
  saving: "tailored.msgs_saving",
};

const PHASE_STEPS: { key: string; labelKey: MessageKey }[] = [
  { key: "watcher", labelKey: "tailored.phase_watcher" },
  { key: "analyst", labelKey: "tailored.phase_analyst" },
  { key: "reporter", labelKey: "tailored.phase_reporter" },
  { key: "saving", labelKey: "tailored.phase_saving" },
];

export default function TailoredView() {
  const { t } = useI18n();
  const [access, setAccess] = useState<TailoredAccess | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<string | null>(null);
  const [openingCase, setOpeningCase] = useState<string | null>(null);
  const [errorModal, setErrorModal] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fun cycle UX — rotating messages, items-found counter, phase-flash
  const [messageKey, setMessageKey] = useState(0);
  const [itemsFound, setItemsFound] = useState(0);
  const [prevPhase, setPrevPhase] = useState<string | null>(null);
  const [phaseJustCompleted, setPhaseJustCompleted] = useState<string | null>(null);

  // Rotate the message every 2s while running
  useEffect(() => {
    if (!running) return;
    setMessageKey(0);
    const i = setInterval(() => setMessageKey((k) => k + 1), 2000);
    return () => clearInterval(i);
  }, [running, phase]);

  // Items-found counter ticks up during watcher/analyst phases
  useEffect(() => {
    if (!running) {
      setItemsFound(0);
      return;
    }
    const i = setInterval(() => {
      setItemsFound((n) => {
        if (phase === "watcher") return n + Math.ceil(Math.random() * 3);
        if (phase === "analyst") return n + (Math.random() > 0.6 ? 1 : 0);
        return n;
      });
    }, 800);
    return () => clearInterval(i);
  }, [running, phase]);

  // Flash on phase transitions
  useEffect(() => {
    if (phase && prevPhase && phase !== prevPhase) {
      setPhaseJustCompleted(prevPhase);
      const t = setTimeout(() => setPhaseJustCompleted(null), 800);
      return () => clearTimeout(t);
    }
    setPrevPhase(phase);
  }, [phase, prevPhase]);

  // Initial load
  useEffect(() => {
    Promise.all([
      getTailoredAccess().catch(() => null),
      getLatestReport().catch(() => null),
      listCases().catch(() => []),
      getCycleStatus().catch(() => null),
    ])
      .then(([a, r, c, cs]) => {
        setAccess(a);
        setReport(r);
        setCases(c);
        if (cs && cs.status === "running") {
          setRunning(true);
          setPhase(cs.phase || null);
          startPolling();
        }
      })
      .finally(() => setLoading(false));
    return () => stopPolling();
  }, []);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const startPolling = () => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const s = await getCycleStatus();
        if (s.status === "running") {
          setPhase(s.phase || null);
          return;
        }
        if (s.status === "complete") {
          stopPolling();
          setRunning(false);
          setPhase("complete");
          const [r, c, a] = await Promise.all([
            getLatestReport().catch(() => null),
            listCases().catch(() => []),
            getTailoredAccess().catch(() => null),
          ]);
          setReport(r);
          setCases(c);
          setAccess(a);
          return;
        }
        if (s.status === "error") {
          stopPolling();
          setRunning(false);
          setPhase(null);
          setErrorModal(t("tailored.cycle_error"));
          const a = await getTailoredAccess().catch(() => null);
          setAccess(a);
        }
      } catch {
        stopPolling();
        setRunning(false);
      }
    }, 4000);
  };

  const handleRun = async () => {
    setRunning(true);
    setPhase("starting");
    try {
      await runTailoredCycle();
      const a = await getTailoredAccess().catch(() => null);
      setAccess(a);
      startPolling();
    } catch (err) {
      setRunning(false);
      setPhase(null);
      setErrorModal(err instanceof Error ? err.message : t("tailored.start_failed"));
    }
  };

  const handleOpen = async (opp: Opportunity) => {
    setOpeningCase(opp.id);
    try {
      const newCase = await openCaseFromOpportunity(opp.id, opp.cycle_date);
      window.location.href = `/case/${newCase.case_id}`;
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : t("opps.open_failed"));
      setOpeningCase(null);
    }
  };

  if (loading) return <LoadingBar label={t("tailored.loading")} />;

  // Scanner tier — show the upgrade pitch instead of the cycle UI
  if (access?.reason === "upgrade") {
    return <UpgradePitch onError={(m) => setErrorModal(m)} />;
  }

  const opportunities = report?.opportunities ?? [];
  const priorityOrder: Array<"high" | "medium" | "low"> = ["high", "medium", "low"];
  const sorted = priorityOrder.flatMap((p) => opportunities.filter((o) => o.priority === p));
  const cooldown = access?.timer && !access.timer.expired ? access.timer : null;
  const canRun = !!access?.allowed && !running;

  return (
    <div>
      <ErrorModal message={errorModal} onClose={() => setErrorModal(null)} />

      {/* Run / cooldown panel */}
      <div className="card-gradient border border-slate-200 p-5 shadow-sm mb-6">
        {running ? (
          (() => {
            const currentIndex = PHASE_STEPS.findIndex((p) => p.key === phase);
            const progress =
              currentIndex === -1 ? 3 : Math.min(((currentIndex + 0.5) / PHASE_STEPS.length) * 100, 98);
            const msgs = phase && PHASE_MESSAGE_KEYS[phase]
              ? t(PHASE_MESSAGE_KEYS[phase]).split("|")
              : [];
            const currentMsg = msgs.length > 0 ? msgs[messageKey % msgs.length] : "";
            return (
              <div>
                {/* Header with live dot + activity counter */}
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-3 h-3 rounded-full brand-gradient live-dot" />
                    <span className="text-base font-medium text-slate-700">{t("tailored.cycle_running")}</span>
                  </div>
                  {itemsFound > 0 && (
                    <span
                      key={itemsFound}
                      className="text-sm font-mono font-medium text-slate-500 tabular-nums counter-tick"
                    >
                      {t("tailored.items_scanned", { n: itemsFound })}
                    </span>
                  )}
                </div>

                {/* Gradient progress bar */}
                <div className="w-full h-2.5 bg-slate-100 rounded-full overflow-hidden mb-1.5">
                  <div
                    className="h-full rounded-full transition-all duration-1000 ease-out brand-gradient progress-shimmer"
                    style={{ width: `${progress}%` }}
                  />
                </div>

                {/* Live rotating message */}
                {currentMsg && (
                  <p
                    key={`${phase}-${messageKey}`}
                    className="text-[11px] text-slate-400 mb-4 cycle-msg-enter"
                  >
                    {currentMsg}...
                  </p>
                )}

                {/* Phase steps */}
                <div className="space-y-1.5">
                  {PHASE_STEPS.map((p, i) => {
                    const isDone = currentIndex > i;
                    const isActive = currentIndex === i;
                    const justDone = phaseJustCompleted === p.key;
                    return (
                      <div
                        key={p.key}
                        className={`flex items-center gap-2.5 px-2 py-1 ${justDone ? "phase-just-done" : ""}`}
                      >
                        {isDone ? (
                          <span className="text-green-500 text-base w-4 text-center">✓</span>
                        ) : isActive ? (
                          <div className="w-4 flex justify-center">
                            <div className="w-2.5 h-2.5 rounded-full brand-gradient live-dot" />
                          </div>
                        ) : (
                          <div className="w-4 flex justify-center">
                            <div className="w-2 h-2 bg-slate-200 rounded-full" />
                          </div>
                        )}
                        <span
                          className={`text-sm transition-colors duration-300 ${
                            isActive
                              ? "text-slate-800 font-semibold"
                              : isDone
                              ? "text-green-600 font-medium"
                              : "text-slate-400"
                          }`}
                        >
                          {t(p.labelKey)}
                          {isDone && ` — ${t("tailored.done")}`}
                        </span>
                      </div>
                    );
                  })}
                </div>

                <p className="text-[11px] text-slate-400 mt-4">
                  {t("tailored.cycle_hint")}
                </p>
              </div>
            );
          })()
        ) : (
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <h2 className="text-lg font-semibold text-slate-800 mb-1 font-[family-name:var(--font-dm-sans)]">
                {t("tailored.weekly_cycle")}
              </h2>
              <p className="text-sm text-slate-700">
                {t("tailored.weekly_cycle_desc")}
              </p>
              {report && (
                <p className="text-sm text-slate-600 mt-1.5">
                  {t("tailored.last_cycle")}: <strong>{report.date}</strong> · {t("tailored.n_opportunities", { n: opportunities.length })}
                </p>
              )}
            </div>
            <div className="shrink-0">
              {cooldown ? (
                <div className="text-right">
                  <div className="text-sm text-slate-600 mb-1">{t("tailored.next_cycle_in")}</div>
                  <div className="text-base font-medium text-slate-800">
                    {formatRemaining(cooldown.remaining_seconds, t("tailored.ready_now"))}
                  </div>
                </div>
              ) : (
                <button
                  onClick={handleRun}
                  disabled={!canRun}
                  className="px-5 py-2.5 text-base btn-gradient rounded-lg disabled:opacity-50 transition-colors"
                >
                  {t("tailored.run_cycle")} →
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Picks list */}
      {!report && !running ? (
        <div className="text-center py-12 text-base text-slate-600">
          {t("tailored.no_picks_yet")}
        </div>
      ) : sorted.length === 0 && !running ? (
        <div className="text-center py-12 text-base text-slate-600">
          {t("tailored.no_results")}
        </div>
      ) : (
        <div className="space-y-3">
          {sorted.map((opp) => {
            const oppTitleLower = opp.title.toLowerCase();
            const oppWords = oppTitleLower.split(/\s+/).slice(0, 5);
            const existingCase = cases.find((c) => {
              const grantWords = c.grant_id.replace(/-/g, " ").toLowerCase().split(/\s+/);
              const overlap = oppWords.filter((w) => grantWords.includes(w));
              return overlap.length >= 2;
            });
            return (
              <div
                key={opp.id}
                className={`card-gradient border border-slate-200 p-4 shadow-sm hover:shadow-md transition-shadow${opp.expired ? " opacity-50" : ""}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
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
                    {opp.details && opp.details.length > 0 && (
                      <ul className="mt-2 space-y-0.5">
                        {opp.details.map((d, i) => (
                          <li key={i} className="text-sm text-slate-700">- {d}</li>
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
                            {link.title ? link.title.slice(0, 40) + (link.title.length > 40 ? "..." : "") : (() => { try { return new URL(link.url).hostname; } catch { return t("opps.source"); } })()}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="shrink-0">
                    {opp.expired ? (
                      <span className="px-3 py-1.5 text-sm bg-slate-100 text-slate-600 rounded-lg">{t("tailored.expired")}</span>
                    ) : existingCase ? (
                      <a
                        href={`/case/${existingCase.case_id}`}
                        className="px-3 py-1.5 text-sm bg-slate-100 text-slate-600 rounded-lg hover:bg-slate-200 transition-colors"
                      >
                        {t("opps.view_case")}
                      </a>
                    ) : (
                      <button
                        onClick={() => handleOpen(opp)}
                        disabled={openingCase === opp.id}
                        className="px-4 py-1.5 text-sm btn-gradient rounded-lg disabled:opacity-50 transition-colors"
                      >
                        {openingCase === opp.id ? t("tailored.creating") : t("opps.open_case")}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


// --- Upgrade pitch (shown to Scanner-tier users when they click the tab) ---

function UpgradePitch({ onError }: { onError: (msg: string) => void }) {
  const { t } = useI18n();
  const [redirecting, setRedirecting] = useState(false);

  const handleUpgrade = async () => {
    setRedirecting(true);
    try {
      const { url } = await createCheckout("starter");
      window.location.href = url;
    } catch (err) {
      onError(err instanceof Error ? err.message : t("tailored.checkout_failed"));
      setRedirecting(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto">
      {/* Hero */}
      <div className="card-gradient border border-slate-200 p-6 shadow-sm mb-4">
        <div className="flex items-start gap-3 mb-3">
          <div className="shrink-0 w-9 h-9 rounded-lg brand-gradient flex items-center justify-center text-white text-xl">★</div>
          <div className="flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-blue-600 mb-1">
              {t("tiers.officer")}
            </div>
            <h2 className="text-xl font-semibold text-slate-900 font-[family-name:var(--font-dm-sans)]">
              {t("tailored.pitch_title")}
            </h2>
            <p className="text-base text-slate-700 mt-1.5">
              {t("tailored.pitch_body")}
            </p>
          </div>
        </div>

        {/* What you get */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-5">
          {([
            { titleKey: "tailored.feat1_title", descKey: "tailored.feat1_desc" },
            { titleKey: "tailored.feat2_title", descKey: "tailored.feat2_desc" },
            { titleKey: "tailored.feat3_title", descKey: "tailored.feat3_desc" },
            { titleKey: "tailored.feat4_title", descKey: "tailored.feat4_desc" },
          ] as { titleKey: MessageKey; descKey: MessageKey }[]).map((f) => (
            <div key={f.titleKey} className="bg-white/60 border border-slate-200 rounded-lg p-3">
              <div className="text-sm font-semibold text-slate-800 mb-0.5">{t(f.titleKey)}</div>
              <div className="text-[11px] text-slate-700">{t(f.descKey)}</div>
            </div>
          ))}
        </div>

        {/* Pricing + CTA */}
        <div className="mt-5 flex items-center justify-between gap-3 pt-4 border-t border-slate-200">
          <div>
            <div className="text-3xl font-bold text-slate-900 font-[family-name:var(--font-dm-sans)]">
              $9 <span className="text-base font-normal text-slate-700">NZD / {t("tailored.month")}</span>
            </div>
            <div className="text-[11px] text-slate-600">{t("tailored.cancel_anytime")}</div>
          </div>
          <button
            onClick={handleUpgrade}
            disabled={redirecting}
            className="px-5 py-2.5 text-base btn-gradient rounded-lg disabled:opacity-50 transition-colors font-medium"
          >
            {redirecting ? t("tailored.redirecting") : `${t("tailored.upgrade_to_officer")} →`}
          </button>
        </div>
      </div>

      {/* Reassurance */}
      <p className="text-[11px] text-slate-600 text-center px-4">
        {t("tailored.reassurance")}
      </p>
    </div>
  );
}
