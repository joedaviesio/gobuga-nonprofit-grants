"use client";

import { useState, useEffect, useRef } from "react";
import { uploadOrgDocument, listOrgUploads, completeSeedingStep, getToken, verifySession } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";

const DOC_TYPES = [
  { key: "general", label: "General" },
  { key: "annual-reports", label: "Annual Reports" },
  { key: "mission-statements", label: "Mission Statements" },
  { key: "organisational-reviews", label: "Organisational Reviews" },
  { key: "previous-applications", label: "Previous Applications" },
  { key: "financial-statements", label: "Financial Statements" },
] as const;

export default function SeedPage() {
  const [uploads, setUploads] = useState<Record<string, { filename: string; size: number } | null>>({});
  const [uploading, setUploading] = useState<string | null>(null);
  const [error, setError] = useState("");
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    verifySession().then((session) => {
      if (!session) {
        window.location.href = "/login";
      } else if (!session.setup_complete) {
        window.location.href = "/setup";
      } else if (session.seeding_complete) {
        window.location.href = "/";
      }
    });

    // Load existing uploads
    listOrgUploads().then((files) => {
      const map: Record<string, { filename: string; size: number }> = {};
      for (const f of files) {
        for (const dt of DOC_TYPES) {
          if (f.filename.startsWith(dt.key + "_")) {
            map[dt.key] = f;
            break;
          }
        }
      }
      setUploads(map);
    }).catch(() => {});
  }, []);

  const handleUpload = async (docType: string, file: File) => {
    setError("");
    setUploading(docType);
    try {
      const result = await uploadOrgDocument(file, docType);
      setUploads((prev) => ({ ...prev, [docType]: { filename: result.filename, size: result.size } }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(null);
    }
  };

  const handleContinue = async () => {
    try {
      await completeSeedingStep();
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to continue");
    }
  };

  const uploadCount = Object.keys(uploads).length;

  return (
    <div className="min-h-screen auth-bg py-12 relative overflow-hidden">
      <div className="orb orb-yellow" style={{ top: '10%', right: '5%' }} />
      <div className="orb orb-blue" style={{ bottom: '15%', left: '10%' }} />

      <div className="max-w-lg mx-auto relative z-10">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-stone-900">Seed your organisation data</h1>
          <p className="text-base text-stone-700 mt-1">
            Upload key documents to help us understand your organisation better.
            This is optional — you can skip and add them later.
          </p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-base text-red-700 mb-4">{error}</div>
        )}

        <div className="space-y-3">
          {DOC_TYPES.map((dt) => {
            const uploaded = uploads[dt.key];
            const isUploading = uploading === dt.key;

            return (
              <div
                key={dt.key}
                className="card-gradient border border-stone-200/60 backdrop-blur-sm p-4 flex items-center justify-between shadow-sm hover:shadow-md transition-shadow"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-base font-medium text-stone-800">{dt.label}</p>
                  {uploaded && (
                    <p className="text-sm text-stone-600 truncate mt-0.5">
                      {uploaded.filename} ({Math.round(uploaded.size / 1024)}KB)
                    </p>
                  )}
                </div>

                <input
                  ref={(el) => { fileInputRefs.current[dt.key] = el; }}
                  type="file"
                  className="hidden"
                  accept=".pdf,.doc,.docx,.txt,.md,.xlsx,.xls,.csv"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleUpload(dt.key, file);
                    e.target.value = "";
                  }}
                />

                {isUploading ? (
                  <div className="ml-3 w-24">
                    <LoadingBar />
                  </div>
                ) : (
                  <button
                    onClick={() => fileInputRefs.current[dt.key]?.click()}
                    className={`ml-3 px-3 py-1.5 text-base rounded-md border transition-colors ${
                      uploaded
                        ? "bg-green-50 border-green-300 text-green-700 font-medium"
                        : "bg-white border-stone-300 text-stone-700 hover:border-blue-300 hover:bg-blue-50/30"
                    }`}
                  >
                    {uploaded ? "Replace" : "Upload"}
                  </button>
                )}
              </div>
            );
          })}
        </div>

        <div className="mt-6 flex gap-3">
          <button
            onClick={handleContinue}
            className="px-4 py-2.5 text-base border border-stone-300 text-stone-700 rounded-md hover:bg-stone-100 transition-colors"
          >
            Skip for now
          </button>
          <button
            onClick={handleContinue}
            className="flex-1 px-4 py-2.5 text-base btn-gradient rounded-md"
          >
            Continue to Dashboard{uploadCount > 0 ? ` (${uploadCount} uploaded)` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}
