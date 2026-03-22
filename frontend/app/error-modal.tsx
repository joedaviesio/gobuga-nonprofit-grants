"use client";

import { useEffect } from "react";

interface ErrorModalProps {
  message: string | null;
  onClose: () => void;
}

const FRIENDLY_MESSAGES: Record<string, string> = {
  "Case limit reached": "You've reached your case limit. Upgrade to Officer for unlimited cases.",
  "Failed to create case": "We couldn't open that case. Please try again.",
  "Failed to load report": "We couldn't load that report. Please try again.",
  "Failed to save": "Your changes couldn't be saved. Please try again.",
  "Failed to toggle tier": "We couldn't update your tier. Please try again.",
  "Upload failed": "That file couldn't be uploaded. Please try again.",
  "Delete failed": "That file couldn't be deleted. Please try again.",
  "Export failed": "The export didn't work. Please try again.",
};

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
