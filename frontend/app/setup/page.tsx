"use client";

import { useState, useEffect } from "react";
import { setupOrg, getToken, verifySession } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";

const SECTORS = [
  "Access to justice",
  "AI for public good",
  "Digital inclusion",
  "Education",
  "Health & wellbeing",
  "Environment & climate",
  "Community development",
  "Arts & culture",
  "Youth & children",
  "Human rights",
  "Disability services",
  "Housing & homelessness",
  "Food security",
  "Economic empowerment",
  "Technology & innovation",
];

const GEOGRAPHIES = [
  "New Zealand",
  "Australia",
  "United Kingdom",
  "United States",
  "Canada",
  "European Union",
  "Global / International",
  "Pacific Islands",
  "Southeast Asia",
  "Africa",
  "Latin America",
];

type Step = "basics" | "sectors";

export default function SetupPage() {
  const [step, setStep] = useState<Step>("basics");

  // Form data
  const [orgName, setOrgName] = useState("");
  const [country, setCountry] = useState("");
  const [orgStatus, setOrgStatus] = useState<"nonprofit" | "pending" | "forprofit">("nonprofit");
  const [sectors, setSectors] = useState<string[]>([]);
  const [geographies, setGeographies] = useState<string[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Check if already set up
  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    verifySession().then((session) => {
      if (!session) {
        window.location.href = "/login";
      } else if (session.setup_complete) {
        window.location.href = "/";
      } else if (session.org_name) {
        setOrgName(session.org_name);
      }
    });
  }, []);

  const toggleSector = (s: string) => {
    setSectors((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    );
  };

  const toggleGeo = (g: string) => {
    setGeographies((prev) =>
      prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]
    );
  };

  const handleSetup = async () => {
    setLoading(true);
    setError("");
    try {
      await setupOrg({
        org_name: orgName,
        country,
        org_status: orgStatus,
        sectors,
        geographies,
      });
      window.location.href = "/seed";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Setup failed");
    } finally {
      setLoading(false);
    }
  };

  if (step === "basics") {
    return (
      <div className="min-h-screen bg-stone-50 py-12">
        <div className="max-w-lg mx-auto">
          <div className="text-center mb-8">
            <h1 className="text-xl font-bold text-stone-800">Set up your organisation</h1>
            <p className="text-sm text-stone-500 mt-1">Step 1 of 2: Tell us about your org</p>
          </div>

          <div className="bg-white border border-stone-200 rounded-lg p-6 space-y-4">
            {error && (
              <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-xs text-red-700">{error}</div>
            )}

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-1">Organisation name</label>
              <input
                type="text"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-1">Country</label>
              <input
                type="text"
                value={country}
                onChange={(e) => setCountry(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                placeholder="e.g. New Zealand"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-2">Organisation status</label>
              <div className="flex gap-2">
                {([
                  { value: "nonprofit", label: "Non-profit" },
                  { value: "pending", label: "Pending" },
                  { value: "forprofit", label: "For-profit" },
                ] as const).map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setOrgStatus(opt.value)}
                    className={`flex-1 px-3 py-2 text-sm rounded-md border transition-colors ${
                      orgStatus === opt.value
                        ? "bg-blue-600 text-white border-blue-600"
                        : "bg-white text-stone-600 border-stone-300 hover:border-blue-400"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {orgStatus === "forprofit" && (
                <p className="mt-2 text-xs text-amber-600">
                  GoBuga is designed for non-profit organisations. Some grant opportunities may not be applicable to for-profit entities.
                </p>
              )}
            </div>

            <button
              onClick={() => setStep("sectors")}
              disabled={!orgName || !country}
              className="w-full px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              Next: Sectors & Geographies
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (step === "sectors") {
    return (
      <div className="min-h-screen bg-stone-50 py-12">
        <div className="max-w-lg mx-auto">
          <div className="text-center mb-8">
            <h1 className="text-xl font-bold text-stone-800">Sectors & Geographies</h1>
            <p className="text-sm text-stone-500 mt-1">Step 2 of 2: What grants should we look for?</p>
          </div>

          <div className="bg-white border border-stone-200 rounded-lg p-6 space-y-6">
            <div>
              <label className="block text-xs font-medium text-stone-600 mb-2">Funding sectors (select all that apply)</label>
              <div className="flex flex-wrap gap-2">
                {SECTORS.map((s) => (
                  <button
                    key={s}
                    onClick={() => toggleSector(s)}
                    className={`px-3 py-1.5 text-xs rounded-full border transition-colors ${
                      sectors.includes(s)
                        ? "bg-blue-50 border-blue-300 text-blue-700"
                        : "bg-white border-stone-200 text-stone-500 hover:border-stone-300"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-2">Target geographies</label>
              <div className="flex flex-wrap gap-2">
                {GEOGRAPHIES.map((g) => (
                  <button
                    key={g}
                    onClick={() => toggleGeo(g)}
                    className={`px-3 py-1.5 text-xs rounded-full border transition-colors ${
                      geographies.includes(g)
                        ? "bg-blue-50 border-blue-300 text-blue-700"
                        : "bg-white border-stone-200 text-stone-500 hover:border-stone-300"
                    }`}
                  >
                    {g}
                  </button>
                ))}
              </div>
            </div>

            {loading && (
              <LoadingBar label="Setting up your organisation..." />
            )}

            <div className="flex gap-3">
              <button
                onClick={() => setStep("basics")}
                className="px-4 py-2 text-sm border border-stone-200 text-stone-600 rounded-md hover:bg-stone-50"
              >
                Back
              </button>
              <button
                onClick={handleSetup}
                disabled={loading || sectors.length === 0}
                className="flex-1 px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                {loading ? "Setting up..." : "Continue"}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return null;
}
