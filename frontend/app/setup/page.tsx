"use client";

import { useState, useEffect } from "react";
import { setupOrg, runCycle, getCycleStatus, getToken, verifySession } from "@/lib/api";

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

type Step = "basics" | "sectors" | "scanning";

export default function SetupPage() {
  const [step, setStep] = useState<Step>("basics");

  // Form data
  const [orgName, setOrgName] = useState("");
  const [country, setCountry] = useState("");
  const [website, setWebsite] = useState("");
  const [charitableStatus, setCharitableStatus] = useState("");
  const [mission, setMission] = useState("");
  const [sectors, setSectors] = useState<string[]>([]);
  const [geographies, setGeographies] = useState<string[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [scanStatus, setScanStatus] = useState<"starting" | "running" | "complete" | "error">("starting");
  const [scanMessage, setScanMessage] = useState("");

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
        website,
        charitable_status: charitableStatus,
        mission,
        sectors,
        geographies,
      });
      setStep("scanning");
      triggerFirstScan();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Setup failed");
    } finally {
      setLoading(false);
    }
  };

  const triggerFirstScan = async () => {
    setScanStatus("starting");
    try {
      await runCycle();
      setScanStatus("running");
      setScanMessage("Scanning for grant opportunities...");

      const poll = setInterval(async () => {
        try {
          const status = await getCycleStatus();
          if (status.status === "complete") {
            clearInterval(poll);
            setScanStatus("complete");
            setScanMessage(
              `Found ${status.opportunities || 0} opportunities!`
            );
          } else if (status.status === "error") {
            clearInterval(poll);
            setScanStatus("complete");
            setScanMessage("Scan completed with some issues. Your dashboard is ready.");
          }
        } catch {
          // still running
        }
      }, 8000);

      // Timeout after 15 min
      setTimeout(() => {
        clearInterval(poll);
        setScanStatus("complete");
        setScanMessage("Your dashboard is ready.");
      }, 900000);
    } catch {
      // Cycle may already be running or unavailable — still proceed
      setScanStatus("complete");
      setScanMessage("Your dashboard is ready.");
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
              <label className="block text-xs font-medium text-stone-600 mb-1">Website (optional)</label>
              <input
                type="url"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                placeholder="https://yourorg.com"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-1">Charitable status (optional)</label>
              <input
                type="text"
                value={charitableStatus}
                onChange={(e) => setCharitableStatus(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                placeholder="e.g. Registered Charity, 501(c)(3)"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-1">Mission statement</label>
              <textarea
                value={mission}
                onChange={(e) => setMission(e.target.value)}
                rows={3}
                className="w-full px-3 py-2 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                placeholder="What does your organisation do? Who do you serve?"
              />
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
                {loading ? "Setting up..." : "Start scanning for grants"}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Step: scanning
  return (
    <div className="min-h-screen bg-stone-50 flex items-center justify-center">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-bold text-stone-800 mb-2">
          {scanStatus === "complete" ? "You're all set!" : "Scanning for grants..."}
        </h1>

        {scanStatus === "running" || scanStatus === "starting" ? (
          <div className="space-y-4">
            <div className="flex justify-center gap-1 my-6">
              <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
            <p className="text-sm text-stone-500">
              Our AI is scanning the web for grant opportunities matched to your organisation.
              This usually takes 5-10 minutes.
            </p>
            <div className="text-xs text-stone-400 space-y-1">
              <p>Grant Watcher scanning web sources...</p>
              <p>Grant Analyst will assess fit...</p>
              <p>Grant Reporter will compile your brief...</p>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mx-auto">
              <svg className="w-6 h-6 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-sm text-stone-600">{scanMessage}</p>
            <a
              href="/"
              className="inline-block px-6 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
            >
              Go to Dashboard
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
