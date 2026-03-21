const BASE = "/api";
// In production, BOT_BASE should also go through the Next.js rewrite proxy
// to avoid cross-origin issues. Only fall back to direct backend in local dev.
const BOT_BASE = process.env.NEXT_PUBLIC_BOT_URL
  ? `${process.env.NEXT_PUBLIC_BOT_URL}/api`
  : (process.env.NEXT_PUBLIC_API_URL ? "/api" : "http://localhost:8102/api");

// --- Auth helpers ---

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("gobuga_token");
}

export function setToken(token: string) {
  localStorage.setItem("gobuga_token", token);
}

export function clearToken() {
  localStorage.removeItem("gobuga_token");
}

async function request<T>(path: string, opts?: RequestInit & { direct?: boolean }): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(opts?.headers as Record<string, string> || {}),
  };
  const base = opts?.direct ? BOT_BASE : BASE;
  const res = await fetch(`${base}${path}`, { ...opts, headers });
  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// --- Auth ---

export interface AuthResponse {
  token: string;
  user_id: string;
  org_id: string;
  org_name: string;
  setup_complete: boolean;
  expires_in: number;
}

export const registerAccount = async (email: string, password: string, orgName: string, websiteUrl: string = ""): Promise<AuthResponse> => {
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, org_name: orgName, website_url: websiteUrl }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: "Registration failed" }));
    throw new Error(data.detail || "Registration failed");
  }
  const data = await res.json();
  setToken(data.token);
  return data;
};

export const login = async (email: string, password: string): Promise<AuthResponse> => {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: "Invalid email or password" }));
    throw new Error(data.detail || "Invalid email or password");
  }
  const data = await res.json();
  setToken(data.token);
  return data;
};

export interface CycleTimer {
  triggered_at: string;
  expires_at: string;
  remaining_seconds: number;
  expired: boolean;
}

export interface VerifyResponse {
  valid: boolean;
  user_id: string;
  org_id: string;
  org_name: string;
  setup_complete: boolean;
  seeding_complete: boolean;
  tier: "scanner" | "officer";
  tier_label: string;
  cycle_timer: CycleTimer | null;
}

export const verifySession = async (): Promise<VerifyResponse | null> => {
  const token = getToken();
  if (!token) return null;
  try {
    const res = await fetch(`${BASE}/auth/verify`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
};

export const logout = async () => {
  const token = getToken();
  if (token) {
    await fetch(`${BASE}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {});
  }
  clearToken();
};

// --- Org Setup ---

export interface OrgSetupData {
  org_name: string;
  country: string;
  website?: string;
  charitable_status?: string;
  mission?: string;
  sectors?: string[];
  geographies?: string[];
}

export const setupOrg = (data: OrgSetupData) =>
  request<{ org_id: string; profile_generated: boolean; prompts_generated: string[]; setup_complete: boolean }>("/org/setup", {
    method: "POST",
    body: JSON.stringify(data),
  });

export interface OrgProfile {
  org_id: string;
  name: string;
  plan: string;
  setup_complete: boolean;
  country?: string;
  website?: string;
  website_url?: string;
  mission?: string;
  sectors?: string[];
  geographies?: string[];
  profile_text: string;
}

export const getOrgProfile = () => request<OrgProfile>("/org/profile");

export const updateOrgProfile = (updates: {
  name?: string;
  country?: string;
  website?: string;
  sectors?: string[];
  geographies?: string[];
}) =>
  request<OrgProfile>("/org/profile", {
    method: "PATCH",
    body: JSON.stringify(updates),
  });

export const getOrgUsage = (date?: string) =>
  request<Record<string, unknown>>(`/org/usage${date ? `?date=${date}` : ""}`);

// --- Org Seeding ---

export const uploadOrgDocument = async (file: File, docType: string) => {
  const form = new FormData();
  form.append("file", file);
  form.append("doc_type", docType);
  const token = getToken();
  const res = await fetch(`${BASE}/org/upload`, {
    method: "POST",
    body: form,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return res.json();
};

export const listOrgUploads = () =>
  request<{ filename: string; size: number }[]>("/org/uploads");

export const deleteOrgUpload = (filename: string) =>
  request<{ deleted: string }>(`/org/uploads/${encodeURIComponent(filename)}`, { method: "DELETE" });

export const completeSeedingStep = () =>
  request<{ ok: boolean }>("/org/seeding-complete", { method: "POST" });

// --- Billing ---

export const createCheckout = (plan: string) =>
  request<{ url: string; session_id: string }>("/billing/checkout", {
    method: "POST",
    body: JSON.stringify({ plan }),
  });

export const getBillingPortal = () =>
  request<{ url: string }>("/billing/portal");

// --- Tier ---

export interface TierInfo {
  tier: "scanner" | "officer";
  tier_label: string;
  cycle_timer: CycleTimer | null;
}

export const toggleTier = () =>
  request<TierInfo>("/org/toggle-tier", { method: "POST" });

export const getTierInfo = () =>
  request<TierInfo & { can_trigger_cycle: boolean; trigger_message: string | null }>("/org/tier");

export const triggerCycle = (password: string) =>
  request<{ ok: boolean; cycle_timer: CycleTimer }>("/org/trigger-cycle", {
    method: "POST",
    body: JSON.stringify({ password }),
  });

// --- Types ---

export interface CaseSummary {
  case_id: string;
  grant_id: string;
  status: string;
  created: string;
  updated: string;
  officer: string;
  sections_count: number;
  uploads_count: number;
}

export interface Section {
  content: string;
  status: string;
  updated: string;
}

export interface CaseFull {
  case_id: string;
  org_id: string;
  grant_id: string;
  status: string;
  created: string;
  updated: string;
  officer: string;
  signoff: Record<string, unknown> | null;
  grant_brief: Record<string, unknown>;
  uploads: { filename: string; uploaded: string; type: string; size_bytes: number }[];
  draft: { sections: Record<string, Section> };
  conversation: Message[];
  exports: { format: string; filename: string; timestamp: string }[];
}

export interface Message {
  role: string;
  content: string;
  timestamp: string;
  actions?: { type: string; input: Record<string, unknown> }[];
  is_brief?: boolean;
}

export interface ChatResponse {
  case_id: string;
  response: string;
  actions: { type: string; input: Record<string, unknown> }[];
}

export interface Opportunity {
  id: string;
  title: string;
  description: string;
  priority: string;
  details: string[];
  deadline: string | null;
  amount: string | null;
  funder: string | null;
  recommendation: string | null;
  cycle_date: string;
  source_urls: { title: string; url: string; type: string }[];
  expired: boolean;
}

export interface Report {
  date: string;
  raw: string;
  sections: Record<string, string>;
  opportunities: Opportunity[];
  evidence_count: number;
  evidence: { id: string; title: string; type: string; severity: string; source_url: string }[];
}

// --- API calls ---

export const listCases = () => request<CaseSummary[]>("/cases");

export const getCase = (id: string) => request<CaseFull>(`/cases/${id}`);

export const createCase = (grant_id: string, grant_brief: Record<string, unknown>) =>
  request<CaseFull>("/cases", {
    method: "POST",
    body: JSON.stringify({ grant_id, grant_brief }),
  });

export const updateCase = (caseId: string, updates: Record<string, unknown>) =>
  request<CaseFull>(`/cases/${caseId}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });

export const sendMessage = async (caseId: string, message: string): Promise<ChatResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);
  try {
    return await request<ChatResponse>(`/cases/${caseId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
};

export const getDraft = (caseId: string) =>
  request<{ sections: Record<string, Section> }>(`/cases/${caseId}/draft`);

export const updateSection = (caseId: string, section: string, content: string, status: string = "draft") =>
  request<Section>(`/cases/${caseId}/draft/${section}`, {
    method: "PATCH",
    body: JSON.stringify({ content, status }),
  });

export const exportDraft = (caseId: string, format: string = "markdown", templateFilename?: string) =>
  request<{ format: string; content: string; file: string; filename: string }>(`/cases/${caseId}/export`, {
    method: "POST",
    body: JSON.stringify({ format, template_filename: templateFilename }),
  });

export const uploadFile = async (caseId: string, file: File, fileType: string = "document") => {
  const form = new FormData();
  form.append("file", file);
  form.append("file_type", fileType);
  const token = getToken();
  const res = await fetch(`${BASE}/cases/${caseId}/upload`, {
    method: "POST",
    body: form,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return res.json();
};

export const signoffCase = (caseId: string, approved: boolean, note: string = "") =>
  request<CaseFull>(`/cases/${caseId}/signoff`, {
    method: "POST",
    body: JSON.stringify({ approved, note, signer: "grants_officer" }),
  });

// --- Reports ---

export const getLatestReport = () => request<Report>("/reports/latest");

export const getReport = (date: string) => request<Report>(`/reports/${date}`);

export const listReportDates = () => request<string[]>("/reports");

export const openCaseFromOpportunity = (opportunityId: string, cycleDate: string) =>
  request<CaseFull>("/reports/open-case", {
    method: "POST",
    body: JSON.stringify({ opportunity_id: opportunityId, cycle_date: cycleDate }),
  });

// --- Cycle ---

export const runCycle = (date?: string) =>
  request<{ status: string; date: string }>("/cycle/run", {
    method: "POST",
    body: JSON.stringify({ date }),
  });

export const getCycleStatus = () =>
  request<{ status: string; date: string; opportunities?: number; phase?: string }>("/cycle/status");

// --- Bots ---

export interface BotUsage {
  input_tokens: number;
  output_tokens: number;
  api_calls: number;
  cost_usd: number;
  model?: string;
}

export interface BotSummaryResponse {
  case_id: string;
  summary: string;
  databank_seeded: number;
  usage?: BotUsage;
}

export interface ParsedSection {
  id: string;
  label: string;
  field_type: string;
  max_words?: number;
  guidance?: string;
}

export interface BotParseResponse {
  case_id: string;
  sections_found: number;
  sections: ParsedSection[];
  usage?: BotUsage;
}

export interface BotFillResponse {
  case_id: string;
  filled: number;
  needs_review: number;
  sections: { section_id: string; confidence: string; review_note: string }[];
  usage?: BotUsage;
}

export interface BotParseAndFillResponse {
  case_id: string;
  parsed: number;
  filled: number;
  needs_review: number;
  sections: { section_id: string; confidence: string; review_note: string }[];
  usage?: BotUsage;
}

export interface BotQuestionsResponse {
  case_id: string;
  response: string;
  usage?: BotUsage;
}

export interface BotAnswerResponse {
  case_id: string;
  response: string;
  saved_entries: number;
  usage?: BotUsage;
}

export interface BotIngestResponse {
  case_id: string;
  entries_added: number;
  summary: string;
  usage?: BotUsage;
}

export interface DatabankEntry {
  id: string;
  key: string;
  value: string;
  source: string;
  category: string;
  added?: string;
  updated?: string;
}

const botTimeout = 300000;

export const botSummary = async (caseId: string): Promise<BotSummaryResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), botTimeout);
  try {
    return await request<BotSummaryResponse>(`/cases/${caseId}/bot/summary`, {
      method: "POST",
      signal: controller.signal,
      direct: true,
    });
  } finally {
    clearTimeout(timeout);
  }
};

export const botParse = async (caseId: string, filename: string): Promise<BotParseResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), botTimeout);
  try {
    return await request<BotParseResponse>(`/cases/${caseId}/bot/parse`, {
      method: "POST",
      body: JSON.stringify({ filename }),
      signal: controller.signal,
      direct: true,
    });
  } finally {
    clearTimeout(timeout);
  }
};

export const botFill = async (caseId: string): Promise<BotFillResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), botTimeout);
  try {
    return await request<BotFillResponse>(`/cases/${caseId}/bot/fill`, {
      method: "POST",
      signal: controller.signal,
      direct: true,
    });
  } finally {
    clearTimeout(timeout);
  }
};

export const botParseAndFill = async (caseId: string, filename: string): Promise<BotParseAndFillResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), botTimeout);
  try {
    return await request<BotParseAndFillResponse>(`/cases/${caseId}/bot/parse-and-fill`, {
      method: "POST",
      body: JSON.stringify({ filename }),
      signal: controller.signal,
      direct: true,
    });
  } finally {
    clearTimeout(timeout);
  }
};

export const botQuestions = async (caseId: string): Promise<BotQuestionsResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), botTimeout);
  try {
    return await request<BotQuestionsResponse>(`/cases/${caseId}/bot/questions`, {
      method: "POST",
      signal: controller.signal,
      direct: true,
    });
  } finally {
    clearTimeout(timeout);
  }
};

export const botAnswerStream = async (
  caseId: string,
  message: string,
  onChunk: (text: string) => void,
  onStatus?: (text: string) => void,
): Promise<BotAnswerResponse> => {
  const token = getToken();
  const base = BOT_BASE;
  const res = await fetch(`${base}/cases/${caseId}/bot/answer/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message }),
  });
  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: BotAnswerResponse | null = null;
  let fullText = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.type === "text") {
          fullText += data.content;
          onChunk(data.content);
        } else if (data.type === "status" && onStatus) {
          onStatus(data.content);
        } else if (data.type === "done") {
          finalResult = {
            case_id: caseId,
            response: fullText,
            saved_entries: data.saved_entries || 0,
            usage: data.usage,
          };
        } else if (data.type === "error") {
          throw new Error(data.content);
        }
      } catch (e) {
        if (e instanceof Error && e.message !== "Unexpected end of JSON input") throw e;
      }
    }
  }

  return finalResult || { case_id: caseId, response: fullText, saved_entries: 0 };
};

export const botAnswer = async (caseId: string, message: string): Promise<BotAnswerResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), botTimeout);
  try {
    return await request<BotAnswerResponse>(`/cases/${caseId}/bot/answer`, {
      method: "POST",
      body: JSON.stringify({ message }),
      signal: controller.signal,
      direct: true,
    });
  } finally {
    clearTimeout(timeout);
  }
};

export const botIngest = async (caseId: string, filename: string): Promise<BotIngestResponse> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), botTimeout);
  try {
    return await request<BotIngestResponse>(`/cases/${caseId}/bot/ingest`, {
      method: "POST",
      body: JSON.stringify({ filename }),
      signal: controller.signal,
      direct: true,
    });
  } finally {
    clearTimeout(timeout);
  }
};

// --- Data Bank ---

export const getDatabank = (caseId: string, category?: string) =>
  request<DatabankEntry[]>(`/cases/${caseId}/databank${category ? `?category=${category}` : ""}`);

export const addDatabankEntry = (caseId: string, key: string, value: string, category: string = "general") =>
  request<DatabankEntry>(`/cases/${caseId}/databank`, {
    method: "POST",
    body: JSON.stringify({ key, value, source: "user_input", category }),
  });

export const deleteDatabankEntry = (caseId: string, key: string) =>
  request<{ deleted: string }>(`/cases/${caseId}/databank/${key}`, {
    method: "DELETE",
  });
