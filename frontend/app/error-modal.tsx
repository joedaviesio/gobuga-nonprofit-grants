"use client";

import { useEffect } from "react";
import { createCheckout } from "@/lib/api";

interface ErrorModalProps {
  message: string | null;
  onClose: () => void;
}

const FRIENDLY_MESSAGES: Record<string, string> = {
  "Case limit reached": "You've reached your case limit. Upgrade to Officer for unlimited cases.",
  "Failed to create case": "We couldn't open that case. Please try again.",
  "Failed to load report": "We couldn't load that report. Please try again.",
  "Failed to save": "Your changes couldn't be saved. Please try again.",
  "Upload failed": "That file couldn't be uploaded. Please try again.",
  "Delete failed": "That file couldn't be deleted. Please try again.",
  "Export failed": "The export didn't work. Please try again.",
};

// Patterns that indicate a tier/upgrade gate (403 from limits.py)
const UPGRADE_PATTERNS = [
  "upgrade to officer",
  "requires grant officer",
  "chat limit reached",
  "case limit reached",
  "this feature requires",
];

function isUpgradeError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return lower.includes("403") && UPGRADE_PATTERNS.some((p) => lower.includes(p));
}

function extractUpgradeMessage(raw: string): string {
  // Pull the actual message from "403: ..." or "403: {"detail":"..."}"
  const cleaned = raw
    .replace(/^\d{3}:\s*/, "")
    .replace(/\{"detail":\s*"(.+?)"\}/, "$1");
  return cleaned;
}

function simplify(raw: string): string {
  // Strip HTTP status codes and JSON wrappers
  const cleaned = raw
    .replace(/^\d{3}:\s*/, "")
    .replace(/\{"detail":\s*"(.+?)"\}/, "$1")
    .replace(/^Failed to \w+ \w+:\s*/, "");

  // Match against known messages
  for (const [key, friendly] of Object.entries(FRIENDLY_MESSAGES)) {
    if (cleaned.toLowerCase().includes(key.toLowerCase())) {
      return friendly;
    }
  }

  // If it still looks like a raw API error, give a generic message
  if (cleaned.includes("{") || cleaned.includes("403") || cleaned.includes("500")) {
    return "Something went wrong. Please try again.";
  }

  return cleaned;
}

export default function ErrorModal({ message, onClose }: ErrorModalProps) {
  useEffect(() => {
    if (!message) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [message, onClose]);

  if (!message) return null;

  const upgrade = isUpgradeError(message);

  const handleUpgrade = async () => {
    try {
      const { url } = await createCheckout("starter");
      window.location.href = url;
    } catch {
      window.location.href = "/settings";
    }
  };

  if (upgrade) {
    const upgradeMsg = extractUpgradeMessage(message);
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
        onClick={onClose}
      >
        <div
          className="bg-white rounded-xl shadow-lg border border-blue-200 max-w-sm w-full mx-4 p-6"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-start gap-3 mb-4">
            <div className="shrink-0 w-8 h-8 rounded-full bg-blue-50 border border-blue-200 flex items-center justify-center">
              <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
              </svg>
            </div>
            <p className="text-sm text-slate-700 pt-1">{upgradeMsg}</p>
          </div>
          <div className="flex justify-end gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-slate-500 hover:text-slate-700 transition-colors"
            >
              Not now
            </button>
            <button
              onClick={handleUpgrade}
              className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              Upgrade
            </button>
          </div>
        </div>
      </div>
    );
  }

  const friendly = simplify(message);

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
          <p className="text-sm text-slate-700 pt-1">{friendly}</p>
        </div>
        <div className="flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors"
          >
            OK
          </button>
        </div>
      </div>
    </div>
  );
}
