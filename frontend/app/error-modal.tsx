"use client";

import { useEffect } from "react";
import { useI18n, type MessageKey } from "@/lib/i18n";

interface ErrorModalProps {
  message: string | null;
  onClose: () => void;
}

// Raw backend messages (always English) mapped to translatable friendly keys.
const FRIENDLY_MESSAGES: Record<string, MessageKey> = {
  "Daily limit reached": "errors.daily_limit",
  "Case limit reached": "errors.case_limit",
  "Failed to create case": "errors.create_case",
  "Failed to load report": "errors.load_report",
  "Failed to save": "errors.save",
  "Upload failed": "errors.upload",
  "Delete failed": "errors.delete",
  "Export failed": "errors.export",
};

/**
 * Errors that should interrupt with this modal rather than show inline:
 * a 403 limit or feature gate, or the 429 daily LLM budget. Request errors
 * arrive as "<status>: <body>" (see request() in lib/api.ts).
 */
export function isModalError(raw: string): boolean {
  return raw.includes("403") || raw.startsWith("429:");
}

function simplify(raw: string, t: (key: MessageKey) => string): string {
  // Strip HTTP status codes and JSON wrappers
  const cleaned = raw
    .replace(/^\d{3}:\s*/, "")
    .replace(/\{"detail":\s*"(.+?)"\}/, "$1")
    .replace(/^Failed to \w+ \w+:\s*/, "");

  // Match against known messages
  for (const [key, friendlyKey] of Object.entries(FRIENDLY_MESSAGES)) {
    if (cleaned.toLowerCase().includes(key.toLowerCase())) {
      return t(friendlyKey);
    }
  }

  // If it still looks like a raw API error, give a generic message
  if (cleaned.includes("{") || cleaned.includes("403") || cleaned.includes("500")) {
    return t("errors.generic");
  }

  return cleaned;
}

export default function ErrorModal({ message, onClose }: ErrorModalProps) {
  const { t } = useI18n();
  useEffect(() => {
    if (!message) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [message, onClose]);

  if (!message) return null;

  const friendly = simplify(message, t);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-lg border border-slate-200 max-w-sm w-full mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 mb-4">
          <div className="shrink-0 w-8 h-8 rounded-full bg-red-50 border border-red-200 flex items-center justify-center">
            <svg className="w-4 h-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p className="text-base text-slate-700 pt-1">{friendly}</p>
        </div>
        <div className="flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-base bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors"
          >
            {t("common.ok")}
          </button>
        </div>
      </div>
    </div>
  );
}
