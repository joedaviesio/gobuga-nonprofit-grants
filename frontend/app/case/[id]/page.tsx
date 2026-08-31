"use client";

import { useEffect, useState, useRef, use } from "react";
import {
  getCase,
  updateCase,
  exportDraft,
  uploadFile,
  botParseAndFill,
  botQuestions,
  botAnswerStream,
  botIngest,
  getDatabank,
  getToken,
  type CaseFull,
  type Message,
  type DatabankEntry,
} from "@/lib/api";
import DOMPurify from "dompurify";
import ErrorModal from "@/app/error-modal";
import { useI18n } from "@/lib/i18n";

const API_BASE = "/api";

// The three user paths after seeing the summary
type FlowPath = null | "upload_file" | "upload_submission" | "ask_questions";
type BotStatus = "idle" | "working" | "done" | "error";

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function SimpleMarkdown({ text }: { text: string }) {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const inlineMd = (s: string) => s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Process tables separately, then inline markdown for everything else
  const lines = text.split("\n");
  const output: string[] = [];
  let inTable = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    const isTableRow = /^\|(.+)\|$/.test(line);
    const isSeparator = /^\|[-:|  ]+\|$/.test(line);

    if (isTableRow && !inTable) {
      inTable = true;
      const cells = line.split("|").filter((c) => c.trim()).map((c) => inlineMd(esc(c.trim())));
      output.push('<table class="text-sm border-collapse w-full my-2"><thead><tr>' +
        cells.map((c) => `<th class="border border-stone-200 px-2 py-1 bg-stone-50 text-left font-medium">${c}</th>`).join("") +
        "</tr></thead><tbody>");
    } else if (inTable && isSeparator) {
      // skip separator row
    } else if (inTable && isTableRow) {
      const cells = line.split("|").filter((c) => c.trim()).map((c) => inlineMd(esc(c.trim())));
      output.push("<tr>" +
        cells.map((c) => `<td class="border border-stone-200 px-2 py-1">${c}</td>`).join("") +
        "</tr>");
    } else {
      if (inTable) {
        output.push("</tbody></table>");
        inTable = false;
      }
      // Apply standard markdown to non-table lines
      const html = esc(line)
        .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-blue-600 underline hover:text-blue-800">$1</a>')
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.+?)\*/g, "<em>$1</em>")
        .replace(/`(.+?)`/g, "<code>$1</code>")
        .replace(/^### (.+)$/, '<h3>$1</h3>')
        .replace(/^## (.+)$/, '<h2>$1</h2>')
        .replace(/^# (.+)$/, '<h1>$1</h1>')
        .replace(/^- (.+)$/, "<li>$1</li>");
      output.push(html);
    }
  }
  if (inTable) output.push("</tbody></table>");

  const html = output.join("<br>");
  return <div className="prose text-base" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />;
}

import LoadingBar from "@/app/loading-bar";

function LoadingDots({ label }: { label: string }) {
  return (
    <div className="p-3 bg-stone-50 rounded-lg">
      <LoadingBar label={label} />
    </div>
  );
}


export default function CaseDetail({ params }: { params: Promise<{ id: string }> }) {
  const { t } = useI18n();
  const { id } = use(params);
  const [caseData, setCaseData] = useState<CaseFull | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [flowPath, setFlowPath] = useState<FlowPath>(null);
  const [botStatus, setBotStatus] = useState<BotStatus>("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [qaInput, setQaInput] = useState("");
  const [qaMessages, setQaMessages] = useState<{ role: string; content: string }[]>([]);
  const [databank, setDatabank] = useState<DatabankEntry[]>([]);
  const [sidebarTab, setSidebarTab] = useState<"brief" | "databank" | "draft">("brief");
  const [totalUsage, setTotalUsage] = useState({ input_tokens: 0, output_tokens: 0, api_calls: 0, cost_usd: 0 });
  const [submissionFilename, setSubmissionFilename] = useState("");
  const [errorModal, setErrorModal] = useState<string | null>(null);

  const trackUsage = (usage: { input_tokens?: number; output_tokens?: number; api_calls?: number; cost_usd?: number } | undefined) => {
    if (!usage) return;
    setTotalUsage((prev) => ({
      input_tokens: prev.input_tokens + (usage.input_tokens || 0),
      output_tokens: prev.output_tokens + (usage.output_tokens || 0),
      api_calls: prev.api_calls + (usage.api_calls || 0),
      cost_usd: Math.round((prev.cost_usd + (usage.cost_usd || 0)) * 10000) / 10000,
    }));
  };
  const fileInputRef = useRef<HTMLInputElement>(null);
  const submissionInputRef = useRef<HTMLInputElement>(null);
  const qaScrollRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);

  // Mark case as viewed in localStorage
  const markCaseViewed = (caseId: string) => {
    try {
      const viewed = JSON.parse(localStorage.getItem("gobuga_viewed_cases") || "{}");
      viewed[caseId] = new Date().toISOString();
      localStorage.setItem("gobuga_viewed_cases", JSON.stringify(viewed));
    } catch {}
  };

  // Load case data
  useEffect(() => {
    getCase(id).then((data) => {
      setCaseData(data);
      setMessages(data.conversation || []);
      markCaseViewed(id);
    });
    getDatabank(id).then(setDatabank).catch(() => {});
  }, [id]);

  // Auto-scroll: only scroll if user is near the bottom, use instant scroll during streaming
  const lastQaContent = qaMessages[qaMessages.length - 1]?.content;
  const lastQaLength = qaMessages.length;
  useEffect(() => {
    const el = qaScrollRef.current;
    if (!el || !isNearBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [lastQaLength, lastQaContent]);

  const refreshCase = async () => {
    const data = await getCase(id);
    setCaseData(data);
    setMessages(data.conversation || []);
    const db = await getDatabank(id).catch(() => []);
    setDatabank(db);
  };

  // --- Download helper ---
  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExport = async (format: string, templateFilename?: string) => {
    const fileFormats = ["docx", "doc", "pdf", "xlsx", "xls"];
    try {
      const result = await exportDraft(id, format, templateFilename);
      if (fileFormats.includes(format) && result.filename) {
        const token = getToken();
        const res = await fetch(`${API_BASE}/cases/${id}/download/${result.filename}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        downloadBlob(await res.blob(), result.filename);
      } else if (format === "markdown") {
        downloadBlob(new Blob([result.content], { type: "text/markdown" }), `${id}-draft.md`);
      } else {
        downloadBlob(new Blob([JSON.stringify(result.content, null, 2)], { type: "application/json" }), `${id}-draft.json`);
      }
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : t("errors.export"));
    }
  };

  // --- Download summary as DOCX ---
  const handleDownloadSummary = async () => {
    const summaryMsg = messages.find((m) => m.role === "assistant");
    if (!summaryMsg) return;
    const blob = new Blob([summaryMsg.content], { type: "text/markdown" });
    downloadBlob(blob, `${id}-summary.md`);
  };

  // --- Path 1: Upload a file → ingest to databank ---
  const handleUploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (fileInputRef.current) fileInputRef.current.value = "";

    setBotStatus("working");
    setStatusMessage(t("case.adding_to_databank", { name: file.name }));

    try {
      const uploadResult = await uploadFile(id, file, "reference");
      const sanitizedName = uploadResult.filename || file.name;
      const ingestResult = await botIngest(id, sanitizedName);
      trackUsage(ingestResult.usage);
      await refreshCase();
      setBotStatus("done");
      setStatusMessage(t("case.added_to_databank", { name: sanitizedName }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("dashboard.unknown_error");
      if (msg.includes("403")) {
        setErrorModal(msg);
        setBotStatus("idle");
        setStatusMessage("");
      } else {
        setBotStatus("error");
        setStatusMessage(`${t("case.error")}: ${msg}`);
      }
    }
  };

  // --- Path 2: Upload submission → Bot B + C ---
  const handleUploadSubmission = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (submissionInputRef.current) submissionInputRef.current.value = "";

    setBotStatus("working");
    setStatusMessage(t("case.uploading_submission"));

    try {
      const uploadResult = await uploadFile(id, file, "application_form");
      const sanitizedName = uploadResult.filename || file.name;
      setSubmissionFilename(sanitizedName);
      setStatusMessage(t("case.parsing_sections"));
      const result = await botParseAndFill(id, sanitizedName);
      trackUsage(result.usage);
      await refreshCase();
      setBotStatus("done");
      setStatusMessage(
        t("case.parsed_filled", { parsed: result.parsed, filled: result.filled }) +
        (result.needs_review > 0 ? ` ${t("case.need_review", { n: result.needs_review })}` : ` ${t("case.ready_to_export")}`)
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("dashboard.unknown_error");
      if (msg.includes("403")) {
        setErrorModal(msg);
        setBotStatus("idle");
        setStatusMessage("");
      } else {
        setBotStatus("error");
        setStatusMessage(`${t("case.error")}: ${msg}`);
      }
    }
  };

  // --- Path 3: Ask questions → Bot D ---
  const handleStartQuestions = async () => {
    setBotStatus("working");
    setStatusMessage(t("case.analyzing_needs"));

    try {
      const result = await botQuestions(id);
      trackUsage(result.usage);
      setQaMessages([{ role: "assistant", content: result.response }]);
      await refreshCase();
      setBotStatus("idle");
      setStatusMessage("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("dashboard.unknown_error");
      if (msg.includes("403")) {
        setErrorModal(msg);
        setBotStatus("idle");
        setStatusMessage("");
      } else {
        setBotStatus("error");
        setStatusMessage(`${t("case.error")}: ${msg}`);
      }
    }
  };

  const handleQaSend = async () => {
    const msg = qaInput.trim();
    if (!msg || botStatus === "working") return;
    setQaInput("");
    setQaMessages((prev) => [...prev, { role: "user", content: msg }]);
    isNearBottomRef.current = true;
    setBotStatus("working");
    setStatusMessage(t("case.thinking"));

    // Add an empty assistant message that we'll stream into
    setQaMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    try {
      const result = await botAnswerStream(
        id,
        msg,
        (chunk) => {
          // Append each text chunk to the last assistant message
          setQaMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last && last.role === "assistant") {
              updated[updated.length - 1] = { ...last, content: last.content + chunk };
            }
            return updated;
          });
        },
        (status) => {
          setStatusMessage(status);
        },
      );
      trackUsage(result.usage);
      await refreshCase();
      setBotStatus("idle");
      setStatusMessage("");
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : t("dashboard.unknown_error");
      if (errMsg.includes("403")) {
        // Remove the empty assistant message we added for streaming
        setQaMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last && last.role === "assistant" && !last.content) updated.pop();
          return updated;
        });
        setErrorModal(errMsg);
        setBotStatus("idle");
      } else {
        setQaMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last && last.role === "assistant" && !last.content) {
            updated[updated.length - 1] = { ...last, content: `${t("case.error")}: ${errMsg}` };
          } else {
            updated.push({ role: "assistant", content: `${t("case.error")}: ${errMsg}` });
          }
          return updated;
        });
        setBotStatus("error");
      }
      setStatusMessage("");
    }
  };

  // --- Render ---

  if (!caseData) {
    return (
      <div className="p-8 max-w-xs">
        <LoadingBar label={t("case.loading")} />
      </div>
    );
  }

  const sections = caseData.draft?.sections || {};
  const sectionNames = Object.keys(sections);
  const brief = caseData.grant_brief || {};
  const summaryMsg = messages.find((m) => m.role === "assistant");
  const templateUploads = (caseData.uploads || []).filter(
    (u) => (u.filename.endsWith(".docx") || u.filename.endsWith(".xlsx")) && u.type === "application_form"
  );

  return (
    <div className="flex flex-col md:flex-row h-[calc(100vh-49px)] app-bg">
      <ErrorModal message={errorModal} onClose={() => setErrorModal(null)} />
      {/* Hidden file inputs */}
      <input ref={fileInputRef} type="file" className="hidden" accept=".pdf,.docx,.doc,.xlsx,.xls,.html,.htm,.txt,.csv" onChange={handleUploadFile} />
      <input ref={submissionInputRef} type="file" className="hidden" accept=".pdf,.docx,.doc,.xlsx,.xls" onChange={handleUploadSubmission} />

      {/* Left sidebar */}
      <div className="w-full md:w-80 border-b md:border-b-0 md:border-r border-stone-200 flex flex-col bg-white overflow-y-auto md:max-h-none max-h-[40vh]">
        {/* Case header */}
        <div className="p-4 border-b border-stone-200">
          <div className="text-sm font-mono text-stone-600">{caseData.case_id}</div>
          <div className="text-base font-medium text-stone-700 mt-1">{caseData.grant_id}</div>
          {/* Status stepper */}
          {(() => {
            const statuses = [
              { value: "open", label: t("cases.status_open"), color: "bg-blue-500" },
              { value: "submitted", label: t("cases.status_submitted"), color: "bg-amber-500" },
              { value: "accepted", label: t("cases.status_accepted"), color: "bg-green-500" },
              { value: "rejected", label: t("cases.status_rejected"), color: "bg-red-500" },
            ];
            const currentIdx = statuses.findIndex((s) => s.value === caseData.status);
            const isClosed = caseData.status === "closed";

            return (
              <div className="mt-3 space-y-2">
                <div className="flex items-center gap-1">
                  {statuses.map((s, i) => {
                    const isActive = s.value === caseData.status;
                    const isPast = !isClosed && currentIdx >= 0 && i < currentIdx;
                    // Can only move forward in the main flow, or pick accepted/rejected from submitted
                    const isClickable = !isClosed && (
                      (s.value === "submitted" && caseData.status === "open") ||
                      (s.value === "accepted" && caseData.status === "submitted") ||
                      (s.value === "rejected" && caseData.status === "submitted") ||
                      (s.value === "open" && (caseData.status === "submitted" || caseData.status === "accepted" || caseData.status === "rejected"))
                    );

                    return (
                      <button
                        key={s.value}
                        disabled={!isClickable}
                        onClick={async () => {
                          if (!isClickable) return;
                          try {
                            const updated = await updateCase(id, { status: s.value });
                            setCaseData(updated);
                          } catch (err) {
                            setErrorModal(err instanceof Error ? err.message : t("errors.save"));
                          }
                        }}
                        className={`flex-1 py-1 text-xs font-medium rounded transition-colors ${
                          isActive
                            ? `${s.color} text-white`
                            : isPast
                            ? "bg-stone-200 text-stone-700"
                            : isClickable
                            ? "bg-stone-100 text-stone-700 hover:bg-stone-200 cursor-pointer"
                            : "bg-stone-50 text-stone-300 cursor-default"
                        }`}
                        title={isClickable ? t("case.set_to", { status: s.label }) : ""}
                      >
                        {s.label}
                      </button>
                    );
                  })}
                </div>
                {/* Close/reopen toggle */}
                <div className="flex items-center justify-between">
                  <button
                    onClick={async () => {
                      try {
                        const newStatus = isClosed ? "open" : "closed";
                        const updated = await updateCase(id, { status: newStatus });
                        setCaseData(updated);
                      } catch (err) {
                        setErrorModal(err instanceof Error ? err.message : t("errors.save"));
                      }
                    }}
                    className={`text-xs ${
                      isClosed
                        ? "text-blue-500 hover:text-blue-700"
                        : "text-stone-600 hover:text-stone-600"
                    }`}
                  >
                    {isClosed ? t("case.reopen") : t("case.close")}
                  </button>
                  {typeof brief.deadline === "string" && !isNaN(new Date(brief.deadline as string).getTime()) && (
                    <span className="text-xs text-stone-600">
                      {t("case.due", { date: new Date(brief.deadline as string).toLocaleDateString() })}
                    </span>
                  )}
                </div>
                {isClosed && (
                  <div className="text-xs px-2 py-1 bg-stone-100 rounded text-stone-700 text-center">
                    {t("case.closed")}
                  </div>
                )}
              </div>
            );
          })()}

        </div>

        {/* Sidebar tabs */}
        <div className="flex border-b border-stone-200">
          {(["brief", "databank", "draft"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setSidebarTab(tab)}
              className={`flex-1 py-2 text-sm font-medium ${
                sidebarTab === tab
                  ? "text-blue-600 border-b-2 border-blue-600"
                  : "text-stone-600 hover:text-stone-600"
              }`}
            >
              {tab === "brief" ? t("case.tab_brief") :
               tab === "databank" ? `${t("case.tab_databank")} (${databank.length})` :
               `${t("case.tab_draft")} (${sectionNames.length})`}
            </button>
          ))}
        </div>

        {/* Sidebar content */}
        <div className="flex-1 overflow-y-auto p-4">
          {sidebarTab === "brief" && (
            <div className="space-y-3 text-sm">
              {Object.entries(brief).map(([k, v]) => {
                // Skip null/undefined/empty values
                if (v === null || v === undefined) return null;
                if (Array.isArray(v) && v.length === 0) return null;

                return (
                  <div key={k}>
                    <div className="font-medium text-stone-700 mb-0.5">{k.replace(/_/g, " ")}</div>
                    <div className="text-stone-700">
                      {Array.isArray(v) ? (
                        v.every((item) => typeof item === "string") ? (
                          <ul className="space-y-0.5">
                            {v.map((item, i) => (
                              <li key={i}>- {item}</li>
                            ))}
                          </ul>
                        ) : v.every((item) => typeof item === "object" && item !== null && "title" in item && "url" in item) ? (
                          <div className="space-y-1">
                            {v.map((item: { title?: string; url: string }, i: number) => (
                              <a key={i} href={item.url} target="_blank" rel="noopener" className="block text-blue-600 underline hover:text-blue-800 truncate">
                                {item.title || item.url}
                              </a>
                            ))}
                          </div>
                        ) : (
                          <pre className="text-sm bg-stone-50 p-2 rounded overflow-x-auto">
                            {JSON.stringify(v, null, 2)}
                          </pre>
                        )
                      ) : typeof v === "object" ? (
                        <pre className="text-sm bg-stone-50 p-2 rounded overflow-x-auto">
                          {JSON.stringify(v, null, 2)}
                        </pre>
                      ) : (
                        String(v)
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {sidebarTab === "databank" && (
            <div className="space-y-2">
              {databank.length === 0 ? (
                <p className="text-sm text-stone-600">{t("case.databank_empty")}</p>
              ) : (
                (() => {
                  const byCategory: Record<string, DatabankEntry[]> = {};
                  databank.forEach((e) => {
                    const cat = e.category || "general";
                    if (!byCategory[cat]) byCategory[cat] = [];
                    byCategory[cat].push(e);
                  });
                  return Object.entries(byCategory).map(([cat, entries]) => (
                    <div key={cat}>
                      <div className="text-sm font-medium text-stone-700 mb-1 capitalize">{cat.replace(/_/g, " ")}</div>
                      {entries.map((e) => (
                        <div key={e.id} className="p-2 mb-1 bg-stone-50 rounded text-sm">
                          <div className="font-medium text-stone-700">{e.key.replace(/_/g, " ")}</div>
                          <div className="text-stone-700 mt-0.5 line-clamp-2">{e.value}</div>
                          <div className="text-xs text-stone-300 mt-0.5">{e.source}</div>
                        </div>
                      ))}
                    </div>
                  ));
                })()
              )}
              {/* Uploads list */}
              {(caseData.uploads?.length || 0) > 0 && (
                <div className="pt-2 mt-2 border-t border-stone-100">
                  <div className="text-sm font-medium text-stone-700 mb-1">{t("case.documents")} ({caseData.uploads.length})</div>
                  {caseData.uploads.map((u, i) => (
                    <div key={i} className="flex items-center gap-1 text-sm text-stone-600 py-0.5">
                      <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                        u.type === "application_form" ? "bg-blue-400" :
                        u.type === "project_plan" ? "bg-amber-400" :
                        "bg-stone-300"
                      }`} />
                      <span className="truncate">{u.filename}</span>
                      <span className="text-stone-300 text-xs ml-auto">{u.type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {sidebarTab === "draft" && (
            <div className="space-y-2">
              {sectionNames.length === 0 ? (
                <p className="text-sm text-stone-600">{t("case.no_sections")}</p>
              ) : (
                sectionNames.map((name) => {
                  const sec = sections[name];
                  return (
                    <div key={name} className="p-2 border border-stone-200 rounded-md">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-stone-700">
                          {name.replace(/_/g, " ")}
                        </span>
                        <span className={`text-sm px-1.5 py-0.5 rounded ${
                          sec.status === "complete" ? "bg-green-100 text-green-700" :
                          sec.status === "review" ? "bg-amber-100 text-amber-700" :
                          sec.status === "blocked" ? "bg-red-100 text-red-700" :
                          "bg-stone-100 text-stone-700"
                        }`}>
                          {sec.status}
                        </span>
                      </div>
                      <div className="text-sm text-stone-600 mt-1">
                        {sec.content ? t("case.n_words", { n: sec.content.split(" ").length }) : t("case.empty")}
                      </div>
                    </div>
                  );
                })
              )}
              {sectionNames.length > 0 && (
                <div className="space-y-2 mt-3">
                  <div className="text-sm font-medium text-stone-700">{t("case.export")}</div>
                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => handleExport("pdf")} className="text-sm px-2 py-1 bg-stone-100 rounded hover:bg-stone-200">PDF</button>
                    <button onClick={() => handleExport("docx")} className="text-sm px-2 py-1 bg-stone-100 rounded hover:bg-stone-200">DOCX</button>
                    <button onClick={() => handleExport("xlsx")} className="text-sm px-2 py-1 bg-stone-100 rounded hover:bg-stone-200">XLSX</button>
                    <button onClick={() => handleExport("markdown")} className="text-sm px-2 py-1 bg-stone-100 rounded hover:bg-stone-200">MD</button>
                    <button onClick={() => handleExport("json")} className="text-sm px-2 py-1 bg-stone-100 rounded hover:bg-stone-200">JSON</button>
                    {templateUploads.map((u) => {
                      const ext = u.filename.split(".").pop() || "docx";
                      return (
                        <button
                          key={u.filename}
                          onClick={() => handleExport(ext, u.filename)}
                          className="text-sm px-2 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
                          title={`${t("case.fill_template")}: ${u.filename}`}
                        >
                          {t("case.fill")} {u.filename.slice(0, 20)}...
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Right panel — main flow */}
      <div className="flex-1 flex flex-col overflow-y-auto">
        <div className="max-w-3xl mx-auto w-full p-6 space-y-6">

          {/* Step 1: "What would you like to do next?" */}
          {!flowPath && (
            <div className="bg-white border border-stone-200 rounded-lg shadow-sm p-5">
              <h3 className="text-lg font-bold text-stone-800 mb-4">{t("case.what_next")}</h3>
              <div className="grid grid-cols-3 gap-3">
                {/* Upload a file */}
                <button
                  onClick={() => setFlowPath("upload_file")}
                  className="text-left p-4 rounded-lg border-2 border-amber-200 bg-amber-50 hover:border-amber-400 transition-colors"
                >
                  <div className="text-base font-medium text-stone-800">{t("case.upload_file")}</div>
                  <div className="text-sm text-stone-700 mt-1">{t("case.upload_file_desc")}</div>
                </button>

                {/* Upload submission */}
                <button
                  onClick={() => setFlowPath("upload_submission")}
                  className="text-left p-4 rounded-lg border-2 border-teal-200 bg-teal-50 hover:border-teal-400 transition-colors"
                >
                  <div className="text-base font-medium text-stone-800">{t("case.upload_submission")}</div>
                  <div className="text-sm text-stone-700 mt-1">{t("case.upload_submission_desc")}</div>
                </button>

                {/* Ask questions */}
                <button
                  onClick={() => {
                    setFlowPath("ask_questions");
                    handleStartQuestions();
                  }}
                  className="text-left p-4 rounded-lg border-2 border-purple-200 bg-purple-50 hover:border-purple-400 transition-colors"
                >
                  <div className="text-base font-medium text-stone-800">{t("case.ask_questions")}</div>
                  <div className="text-sm text-stone-700 mt-1">{t("case.ask_questions_desc")}</div>
                </button>
              </div>
            </div>
          )}

          {/* Flow path content — renders in same position as the options card */}
          {flowPath && (
            <div className="space-y-4">
              {/* Back button */}
              <button
                onClick={() => {
                  setFlowPath(null);
                  setBotStatus("idle");
                  setStatusMessage("");
                  setQaMessages([]);
                }}
                className="text-sm text-stone-600 hover:text-stone-600"
              >
                &larr; {t("case.back_to_options")}
              </button>

              {/* Path: Upload a file */}
              {flowPath === "upload_file" && (
                <div className="bg-white border border-amber-200 rounded-lg shadow-sm p-5">
                  <h3 className="text-lg font-bold text-stone-800 mb-1">{t("case.upload_file")}</h3>
                  <p className="text-sm text-stone-700 mb-4">
                    {t("case.upload_file_long")}
                  </p>

                  {botStatus === "working" ? (
                    <LoadingDots label={statusMessage} />
                  ) : (
                    <>
                      {statusMessage && (
                        <div className={`text-sm mb-3 p-2 rounded ${
                          botStatus === "error" ? "bg-red-50 text-red-600" : "bg-green-50 text-green-700"
                        }`}>
                          {statusMessage}
                        </div>
                      )}
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        className="w-full py-3 border-2 border-dashed border-stone-300 rounded-lg text-base text-stone-700 hover:border-amber-400 hover:text-amber-600 transition-colors"
                      >
                        {t("case.click_select_file")}
                      </button>
                    </>
                  )}
                </div>
              )}

              {/* Path: Upload submission */}
              {flowPath === "upload_submission" && (
                <div className="bg-white border border-teal-200 rounded-lg shadow-sm p-5">
                  <h3 className="text-lg font-bold text-stone-800 mb-1">{t("case.upload_submission_form")}</h3>
                  <p className="text-sm text-stone-700 mb-4">
                    {t("case.upload_submission_long")}
                  </p>

                  {botStatus === "working" ? (
                    <LoadingDots label={statusMessage} />
                  ) : botStatus === "done" ? (
                    <div className="space-y-3">
                      <div className="text-sm p-3 bg-teal-50 text-teal-700 rounded-lg">
                        {statusMessage}
                      </div>
                      <div className="flex gap-2">
                        {(() => {
                          const ext = submissionFilename.split(".").pop()?.toLowerCase() || "docx";
                          const templateFillable = ["docx", "doc", "xlsx", "xls", "pdf"].includes(ext);
                          const exportFmt = ext === "pdf" ? "pdf" : ["doc", "docx"].includes(ext) ? "docx" : ["xls", "xlsx"].includes(ext) ? "xlsx" : "docx";
                          return (
                            <>
                              <button
                                onClick={() => handleExport(exportFmt, templateFillable ? submissionFilename : undefined)}
                                className="flex-1 py-2 text-base bg-teal-600 text-white rounded-lg hover:bg-teal-700"
                              >
                                {t("case.download_as")} .{exportFmt}
                              </button>
                              {exportFmt !== "pdf" && (
                                <button
                                  onClick={() => handleExport("pdf")}
                                  className="px-4 py-2 text-base bg-stone-100 text-stone-600 rounded-lg hover:bg-stone-200"
                                >
                                  .pdf
                                </button>
                              )}
                              {exportFmt !== "docx" && (
                                <button
                                  onClick={() => handleExport("docx")}
                                  className="px-4 py-2 text-base bg-stone-100 text-stone-600 rounded-lg hover:bg-stone-200"
                                >
                                  .docx
                                </button>
                              )}
                              <button
                                onClick={() => handleExport("markdown")}
                                className="px-4 py-2 text-base bg-stone-100 text-stone-600 rounded-lg hover:bg-stone-200"
                              >
                                .md
                              </button>
                            </>
                          );
                        })()}
                      </div>
                      <button
                        onClick={() => {
                          setBotStatus("idle");
                          setStatusMessage("");
                        }}
                        className="text-sm text-stone-600 hover:text-stone-600"
                      >
                        {t("case.upload_another")}
                      </button>
                    </div>
                  ) : (
                    <>
                      {statusMessage && (
                        <div className="text-sm mb-3 p-2 rounded bg-red-50 text-red-600">
                          {statusMessage}
                        </div>
                      )}
                      <button
                        onClick={() => submissionInputRef.current?.click()}
                        className="w-full py-3 border-2 border-dashed border-stone-300 rounded-lg text-base text-stone-700 hover:border-teal-400 hover:text-teal-600 transition-colors"
                      >
                        {t("case.click_select_submission")}
                      </button>
                    </>
                  )}
                </div>
              )}

              {/* Path: Ask questions */}
              {flowPath === "ask_questions" && (
                <div className="bg-white border border-purple-200 rounded-lg shadow-sm flex flex-col font-[family-name:var(--font-dm-sans)]" style={{ minHeight: "400px" }}>
                  <div className="p-4 border-b border-purple-100">
                    <h3 className="text-lg font-bold text-stone-800">{t("case.info_gathering")}</h3>
                    <p className="text-sm text-stone-700">{t("case.info_gathering_desc")}</p>
                  </div>

                  {/* Q&A messages */}
                  <div
                    ref={qaScrollRef}
                    className="flex-1 overflow-y-auto p-4 space-y-3"
                    onScroll={() => {
                      const el = qaScrollRef.current;
                      if (!el) return;
                      // Consider "near bottom" if within 80px of the bottom
                      isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                    }}
                  >
                    {qaMessages.map((msg, i) => (
                      <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                        <div className={`rounded-lg px-3 py-2 max-w-[85%] ${
                          msg.role === "user"
                            ? "bg-purple-600 text-white"
                            : "bg-stone-50 border border-stone-200"
                        }`}>
                          {msg.role === "user" ? (
                            <p className="text-base whitespace-pre-wrap">{msg.content}</p>
                          ) : (
                            <SimpleMarkdown text={msg.content} />
                          )}
                        </div>
                      </div>
                    ))}
                    {botStatus === "working" && !qaMessages.some((m, i) => i === qaMessages.length - 1 && m.role === "assistant" && m.content) && (
                      <LoadingDots label={statusMessage || t("case.thinking")} />
                    )}
                  </div>

                  {/* Q&A input */}
                  <div className="border-t border-purple-100 p-3">
                    <div className="flex gap-2">
                      <input
                        value={qaInput}
                        onChange={(e) => {
                          const words = e.target.value.trim().split(/\s+/).filter(Boolean);
                          if (words.length <= 150 || e.target.value.length < qaInput.length) {
                            setQaInput(e.target.value);
                          }
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            handleQaSend();
                          }
                        }}
                        placeholder={t("case.type_answer")}
                        className="flex-1 px-3 py-2 text-base border border-stone-300 rounded-md focus:outline-none focus:border-purple-400"
                        disabled={botStatus === "working"}
                      />
                      <button
                        onClick={handleQaSend}
                        disabled={botStatus === "working" || !qaInput.trim()}
                        className="px-4 py-2 text-base bg-purple-600 text-white rounded-md hover:bg-purple-700 disabled:opacity-50"
                      >
                        {t("case.send")}
                      </button>
                    </div>
                    <div className="mt-1 text-right">
                      <span className={`text-xs ${
                        qaInput.trim().split(/\s+/).filter(Boolean).length >= 140
                          ? "text-red-500"
                          : "text-stone-300"
                      }`}>
                        {qaInput.trim() ? qaInput.trim().split(/\s+/).filter(Boolean).length : 0}/150 {t("case.words")}
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Grant Summary */}
          {summaryMsg && (
            <div className="bg-white border border-stone-200 rounded-lg shadow-sm font-[family-name:var(--font-dm-sans)]">
              <div className="p-4 border-b border-stone-100">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-bold text-stone-800">{t("case.grant_summary")}</h2>
                  <button
                    onClick={handleDownloadSummary}
                    className="text-sm px-2.5 py-1 bg-stone-100 text-stone-600 rounded hover:bg-stone-200"
                  >
                    {t("case.download_md")}
                  </button>
                </div>
              </div>
              <div className="p-4">
                <SimpleMarkdown text={summaryMsg.content} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
