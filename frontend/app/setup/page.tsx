"use client";

import { useState, useEffect } from "react";
import { setupOrg, getToken, verifySession } from "@/lib/api";
import { GEOGRAPHIES } from "@/lib/geographies";
import { SECTORS } from "@/lib/sectors";
import LoadingBar from "@/app/loading-bar";

type Step = "basics" | "sectors";

function GradientMesh() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <svg className="absolute inset-0 h-full w-full" preserveAspectRatio="none" viewBox="0 0 1000 1000">
        <defs>
          <radialGradient id="g1" cx="20%" cy="30%" r="50%">
            <stop offset="0%" stopColor="rgba(59,130,246,0.08)" />
            <stop offset="100%" stopColor="rgba(59,130,246,0)" />
          </radialGradient>
          <radialGradient id="g2" cx="80%" cy="70%" r="50%">
            <stop offset="0%" stopColor="rgba(139,92,246,0.06)" />
            <stop offset="100%" stopColor="rgba(139,92,246,0)" />
          </radialGradient>
          <radialGradient id="g3" cx="60%" cy="20%" r="40%">
            <stop offset="0%" stopColor="rgba(14,165,233,0.05)" />
            <stop offset="100%" stopColor="rgba(14,165,233,0)" />
          </radialGradient>
        </defs>
        <rect width="1000" height="1000" fill="url(#g1)">
          <animateTransform attributeName="transform" type="translate" values="0,0;40,20;0,0" dur="18s" repeatCount="indefinite" />
        </rect>
        <rect width="1000" height="1000" fill="url(#g2)">
          <animateTransform attributeName="transform" type="translate" values="0,0;-30,40;0,0" dur="22s" repeatCount="indefinite" />
        </rect>
        <rect width="1000" height="1000" fill="url(#g3)">
          <animateTransform attributeName="transform" type="translate" values="0,0;20,-30;0,0" dur="15s" repeatCount="indefinite" />
        </rect>
      </svg>
    </div>
  );
}

export default function SetupPage() {
  const [step, setStep] = useState<Step>("basics");
  const [attempted, setAttempted] = useState(false);
  const [basicAttempted, setBasicAttempted] = useState(false);

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

  const [expandedCountries, setExpandedCountries] = useState<Set<string>>(
    () => new Set(["__init__", ...GEOGRAPHIES.filter((g) => g.regions?.length).map((g) => g.name)])
  );

  const toggleExpand = (country: string) => {
    setExpandedCountries((prev) => {
      const next = new Set(prev);
      if (next.has(country)) next.delete(country);
      else next.add(country);
      return next;
    });
  };

  const toggleGeo = (g: string) => {
    setGeographies((prev) =>
      prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]
    );
  };

  const toggleWholeCountry = (country: string, regions: string[]) => {
    setGeographies((prev) => {
      // Remove any individual region selections for this country
      const withoutRegions = prev.filter(
        (g) => !g.startsWith(`${country} > `) && g !== country
      );
      // If country was already selected, deselect it
      if (prev.includes(country)) return withoutRegions;
      // Otherwise select the whole country
      return [...withoutRegions, country];
    });
  };

  const toggleRegion = (country: string, region: string, allRegions: string[]) => {
    const prefixed = `${country} > ${region}`;
    setGeographies((prev) => {
      // If whole country is selected, switch to all regions minus this one
      if (prev.includes(country)) {
        const allPrefixed = allRegions
          .filter((r) => r !== region)
          .map((r) => `${country} > ${r}`);
        return [...prev.filter((g) => g !== country), ...allPrefixed];
      }

      if (prev.includes(prefixed)) {
        // Deselect this region
        return prev.filter((g) => g !== prefixed);
      } else {
        // Select this region, then check if all are now selected
        const next = [...prev, prefixed];
        const selectedRegions = next.filter((g) => g.startsWith(`${country} > `));
        if (selectedRegions.length === allRegions.length) {
          // Auto-consolidate to whole country
          return [...next.filter((g) => !g.startsWith(`${country} > `)), country];
        }
        return next;
      }
    });
  };

  const isRegionSelected = (country: string, region: string) => {
    return (
      geographies.includes(country) ||
      geographies.includes(`${country} > ${region}`)
    );
  };

  // Validation
  const missingSectors = sectors.length === 0;
  const missingGeographies = geographies.length === 0;
  const canContinue = !missingSectors && !loading;

  const handleContinue = () => {
    if (!canContinue) {
      setAttempted(true);
      return;
    }
    handleSetup();
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
    const missingName = !orgName.trim();
    const missingCountry = !country.trim();
    const canAdvance = !missingName && !missingCountry;

    return (
      <div className="min-h-screen bg-stone-50 py-12">
        <GradientMesh />
        <div className="max-w-lg mx-auto">
          <div className="text-center mb-8">
            <h1 className="text-xl font-bold text-stone-900">Set up your organisation</h1>
            <p className="text-sm text-stone-600 mt-1">Step 1 of 2: Tell us about your org</p>
          </div>

          <div className="bg-white border border-stone-200 rounded-lg p-6 space-y-4">
            {error && (
              <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-xs text-red-700">{error}</div>
            )}

            <div>
              <label className="block text-xs font-medium text-stone-700 mb-1">Organisation name</label>
              <input
                type="text"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none focus:border-blue-400 text-stone-900 ${
                  basicAttempted && missingName ? "border-red-300 bg-red-50/50" : "border-stone-300"
                }`}
              />
              {basicAttempted && missingName && (
                <p className="text-xs text-red-600 mt-1">Enter your organisation name</p>
              )}
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-700 mb-1">Country</label>
              <input
                type="text"
                value={country}
                onChange={(e) => setCountry(e.target.value)}
                className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none focus:border-blue-400 text-stone-900 ${
                  basicAttempted && missingCountry ? "border-red-300 bg-red-50/50" : "border-stone-300"
                }`}
                placeholder="e.g. New Zealand"
              />
              {basicAttempted && missingCountry && (
                <p className="text-xs text-red-600 mt-1">Enter your country</p>
              )}
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-700 mb-2">Organisation status</label>
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
                        : "bg-white text-stone-700 border-stone-300 hover:border-blue-400"
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
              onClick={() => {
                if (!canAdvance) {
                  setBasicAttempted(true);
                  return;
                }
                setStep("sectors");
              }}
              className="w-full px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
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
        <GradientMesh />
        <div className="max-w-lg mx-auto">
          <div className="text-center mb-8">
            <h1 className="text-xl font-bold text-stone-900">Sectors & Geographies</h1>
            <p className="text-sm text-stone-600 mt-1">Step 2 of 2: What grants should we look for?</p>
          </div>

          <div className="bg-white border border-stone-200 rounded-lg p-6 space-y-6">
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-xs font-medium text-stone-700">Funding sectors (select all that apply)</label>
                {attempted && missingSectors && (
                  <span className="text-xs text-red-600 animate-pulse">Select at least one</span>
                )}
              </div>
              <div className={`flex flex-wrap gap-2 rounded-lg p-0.5 transition-colors ${
                attempted && missingSectors ? "ring-1 ring-red-300 bg-red-50/30 p-2" : ""
              }`}>
                {SECTORS.map((s) => (
                  <button
                    key={s}
                    onClick={() => toggleSector(s)}
                    className={`px-3 py-1.5 text-xs rounded-full border transition-colors ${
                      sectors.includes(s)
                        ? "bg-blue-50 border-blue-300 text-blue-700"
                        : "bg-white border-stone-200 text-stone-600 hover:border-stone-400"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-xs font-medium text-stone-700">Target geographies</label>
                {attempted && missingGeographies && (
                  <span className="text-xs text-amber-600">Recommended: pick at least one</span>
                )}
              </div>
              <div className="flex flex-col gap-3">
                {GEOGRAPHIES.map((geo) => {
                  const hasRegions = geo.regions && geo.regions.length > 0;
                  const isExpanded = hasRegions && (expandedCountries.has(geo.name) || expandedCountries.has("__init__"));
                  const isWholeSelected = geographies.includes(geo.name);
                  const selectedRegionCount = hasRegions
                    ? geo.regions!.filter((r) => geographies.includes(`${geo.name} > ${r}`)).length
                    : 0;
                  const hasAnyRegionSelected = selectedRegionCount > 0;

                  if (!hasRegions) {
                    return (
                      <button
                        key={geo.name}
                        onClick={() => toggleGeo(geo.name)}
                        className={`px-3 py-1.5 text-xs rounded-full border transition-colors self-start ${
                          geographies.includes(geo.name)
                            ? "bg-blue-50 border-blue-300 text-blue-700"
                            : "bg-white border-stone-200 text-stone-600 hover:border-stone-400"
                        }`}
                      >
                        {geo.name}
                      </button>
                    );
                  }

                  return (
                    <div key={geo.name} className="border border-stone-200 rounded-lg p-3">
                      <div className="flex items-center justify-between mb-2">
                        <button
                          onClick={() => toggleExpand(geo.name)}
                          className="flex items-center gap-1.5 text-xs font-medium text-stone-800 hover:text-stone-900 transition-colors"
                        >
                          <svg
                            className={`w-3 h-3 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                            strokeWidth={2}
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                          </svg>
                          {geo.name}
                          {(isWholeSelected || hasAnyRegionSelected) && (
                            <span className="text-blue-500 font-normal">
                              ({isWholeSelected ? "all" : `${selectedRegionCount}`})
                            </span>
                          )}
                        </button>
                        <button
                          onClick={() => toggleWholeCountry(geo.name, geo.regions!)}
                          className={`px-2 py-0.5 text-[10px] rounded border transition-colors ${
                            isWholeSelected
                              ? "bg-blue-50 border-blue-300 text-blue-600"
                              : "bg-white border-stone-200 text-stone-500 hover:border-stone-400"
                          }`}
                        >
                          {isWholeSelected ? "Deselect all" : "Select all"}
                        </button>
                      </div>
                      {isExpanded && (
                        <div className="flex flex-wrap gap-1.5">
                          {geo.regions!.map((region) => (
                            <button
                              key={region}
                              onClick={() => toggleRegion(geo.name, region, geo.regions!)}
                              className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                                isRegionSelected(geo.name, region)
                                  ? "bg-blue-50 border-blue-300 text-blue-700"
                                  : "bg-white border-stone-200 text-stone-500 hover:border-stone-400"
                              }`}
                            >
                              {region}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {loading && (
              <LoadingBar label="Setting up your organisation..." />
            )}

            <div className="flex gap-3">
              <button
                onClick={() => setStep("basics")}
                className="px-4 py-2 text-sm border border-stone-200 text-stone-700 rounded-md hover:bg-stone-50"
              >
                Back
              </button>
              <button
                onClick={handleContinue}
                className={`flex-1 px-4 py-2 text-sm rounded-md transition-colors ${
                  canContinue
                    ? "bg-blue-600 text-white hover:bg-blue-700"
                    : "bg-blue-600/50 text-white/80 cursor-default"
                }`}
              >
                {loading ? "Setting up..." : "Continue"}
              </button>
            </div>

            {attempted && (missingSectors || missingGeographies) && (
              <div className="bg-amber-50 border border-amber-200 rounded-md px-3 py-2 text-xs text-amber-800 space-y-1">
                <p className="font-medium">Almost there — just a couple things:</p>
                <ul className="list-disc list-inside space-y-0.5 text-amber-700">
                  {missingSectors && <li>Pick at least one <strong>funding sector</strong></li>}
                  {missingGeographies && <li>Pick at least one <strong>target geography</strong> (recommended)</li>}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return null;
}
