"use client";

import { useEffect, useState } from "react";
import { getOrgProfile, createCheckout, getBillingPortal, updateOrgProfile, listOrgUploads, uploadOrgDocument, deleteOrgUpload, getTailoredAccess, toggleTailored, type OrgProfile, type TailoredAccess } from "@/lib/api";
import { getEnabledCountries, findCountry } from "@/lib/countries";
import LoadingBar from "@/app/loading-bar";
import ErrorModal from "@/app/error-modal";
import AuthGate, { useAuth } from "../auth-gate";

import { getDeploymentConfig } from "@/lib/countries";

function getTierInfo(tierKey: string): { label: string; price: string; features: string[] } {
  const config = getDeploymentConfig();
  const tierDef = (config.tiers as Record<string, { label?: string; price_monthly?: number; max_open_cases?: number; chat_messages_per_case?: number; bots_bcd?: boolean; export_docx?: boolean }>)?.[tierKey];

  if (!tierDef) {
    // Fallback for when config hasn't loaded yet
    return tierKey === "officer"
      ? { label: "Grant Officer", price: "Paid", features: ["Tailored Picks", "Unlimited cases & chat", "All bots", "DOCX export"] }
      : { label: "Grant Scanner", price: "Free", features: ["Search grants", "Open cases", "Chat with bots", "Markdown export"] };
  }

  const price = tierDef.price_monthly === 0 ? "Free" : `$${tierDef.price_monthly} ${config.currency}/mo`;
  const cases = tierDef.max_open_cases === -1 ? "Unlimited open cases" : `${tierDef.max_open_cases} open cases at a time`;
  const chat = tierDef.chat_messages_per_case === -1 ? "Unlimited chat" : `${tierDef.chat_messages_per_case} chat messages per case`;
  const features: string[] = [
    `Search ${config.countryLabel} grants`,
    cases,
    chat,
  ];
  if (tierDef.bots_bcd) features.push("Parse & Fill bots");
  if (tierDef.export_docx) features.push("DOCX export");
  if (tierKey === "officer") features.unshift("Weekly Tailored Picks (profile-steered)");

  return { label: tierDef.label || tierKey, price, features };
}

// Legacy compatibility — used in a few places below
const TIER_INFO = {
  scanner: { label: "Grant Scanner", price: "Free", features: [] as string[] },
  officer: { label: "Grant Officer", price: "Paid", features: [] as string[] },
};

function SettingsContent() {
  const { session, refreshSession } = useAuth();
  const [org, setOrg] = useState<OrgProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [checkingOut, setCheckingOut] = useState(false);
  const [checkoutMessage, setCheckoutMessage] = useState<string | null>(null);

  // Uploads state
  const [uploads, setUploads] = useState<{ filename: string; size: number }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [deletingFile, setDeletingFile] = useState<string | null>(null);
  const [uploadDocType, setUploadDocType] = useState("general");
  const [errorModal, setErrorModal] = useState<string | null>(null);

  // Tailored Opportunities state
  const [tailored, setTailored] = useState<TailoredAccess | null>(null);
  const [savingTailored, setSavingTailored] = useState(false);

  // Editing state
  const [editing, setEditing] = useState<string | null>(null); // "name" | "country" | "website" | "sectors" | "geographies"
  const [editValue, setEditValue] = useState("");
  const [editSectors, setEditSectors] = useState<string[]>([]);
  const [editGeographies, setEditGeographies] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([
      getOrgProfile().catch(() => null),
      listOrgUploads().catch(() => []),
      getTailoredAccess().catch(() => null),
    ]).then(([o, files, t]) => {
      setOrg(o);
      setUploads(files);
      setTailored(t);
      setLoading(false);
    });
  }, []);

  const handleToggleTailored = async (enabled: boolean) => {
    setSavingTailored(true);
    try {
      await toggleTailored(enabled);
      const fresh = await getTailoredAccess();
      setTailored(fresh);
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : "Failed to update toggle");
    } finally {
      setSavingTailored(false);
    }
  };

  const formatRemaining = (seconds: number): string => {
    if (seconds <= 0) return "ready now";
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    if (days > 0) return `${days}d ${hours}h`;
    const minutes = Math.floor((seconds % 3600) / 60);
    return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
  };

  const startEdit = (field: string) => {
    if (!org) return;
    if (field === "sectors") {
      setEditSectors([...(org.sectors || [])]);
    } else if (field === "geographies") {
      setEditGeographies([...(org.geographies || [])]);
    } else {
      const val = field === "name" ? org.name : field === "country" ? (org.country || "") : (org.website_url || org.website || "");
      setEditValue(val);
    }
    setEditing(field);
  };

  const cancelEdit = () => {
    setEditing(null);
    setEditValue("");
  };

  const saveEdit = async () => {
    if (!org) return;
    setSaving(true);
    try {
      const updates: Record<string, unknown> = {};
      if (editing === "sectors") {
        updates.sectors = editSectors;
      } else if (editing === "geographies") {
        updates.geographies = editGeographies;
      } else if (editing === "name") {
        updates.name = editValue;
      } else if (editing === "country") {
        updates.country = editValue;
      } else if (editing === "website") {
        updates.website = editValue;
      }
      const updated = await updateOrgProfile(updates as Parameters<typeof updateOrgProfile>[0]);
      // Refresh full profile
      const fresh = await getOrgProfile().catch(() => null);
      if (fresh) setOrg(fresh);
      cancelEdit();
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const toggleEditSector = (s: string) => {
    setEditSectors((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    );
  };

  const toggleEditGeo = (g: string) => {
    setEditGeographies((prev) =>
      prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]
    );
  };

  const toggleEditWholeCountry = (country: string, regions: string[]) => {
    setEditGeographies((prev) => {
      const withoutRegions = prev.filter(
        (g) => !g.startsWith(`${country} > `) && g !== country
      );
      if (prev.includes(country)) return withoutRegions;
      return [...withoutRegions, country];
    });
  };

  const toggleEditRegion = (country: string, region: string, allRegions: string[]) => {
    const prefixed = `${country} > ${region}`;
    setEditGeographies((prev) => {
      if (prev.includes(country)) {
        const allPrefixed = allRegions
          .filter((r) => r !== region)
          .map((r) => `${country} > ${r}`);
        return [...prev.filter((g) => g !== country), ...allPrefixed];
      }
      if (prev.includes(prefixed)) {
        return prev.filter((g) => g !== prefixed);
      } else {
        const next = [...prev, prefixed];
        const selectedRegions = next.filter((g) => g.startsWith(`${country} > `));
        if (selectedRegions.length === allRegions.length) {
          return [...next.filter((g) => !g.startsWith(`${country} > `)), country];
        }
        return next;
      }
    });
  };

  const isEditRegionSelected = (country: string, region: string) => {
    return (
      editGeographies.includes(country) ||
      editGeographies.includes(`${country} > ${region}`)
    );
  };

  // Handle ?checkout=success/cancel query params
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const checkout = params.get("checkout");
    if (checkout === "success") {
      setCheckoutMessage("Upgrade successful! Welcome to Grant Officer.");
      refreshSession();
      // Clean up URL
      window.history.replaceState({}, "", "/settings");
    } else if (checkout === "cancel") {
      setCheckoutMessage(null);
      window.history.replaceState({}, "", "/settings");
    }
  }, []);

  const handleUpgrade = async () => {
    setCheckingOut(true);
    try {
      const { url } = await createCheckout("starter");
      window.location.href = url;
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : "Failed to start checkout");
      setCheckingOut(false);
    }
  };

  const handleManageBilling = async () => {
    try {
      const { url } = await getBillingPortal();
      window.location.href = url;
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : "Failed to open billing portal");
    }
  };

  const handleUploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await uploadOrgDocument(file, uploadDocType);
      const fresh = await listOrgUploads().catch(() => []);
      setUploads(fresh);
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleDeleteFile = async (filename: string) => {
    setDeletingFile(filename);
    try {
      await deleteOrgUpload(filename);
      setUploads((prev) => prev.filter((f) => f.filename !== filename));
    } catch (err) {
      setErrorModal(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeletingFile(null);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen app-bg">
        <div className="max-w-3xl mx-auto px-6 py-8">
          <LoadingBar label="Loading settings..." />
        </div>
      </div>
    );
  }

  const currentTier = session?.tier || "scanner";
  const tierInfo = getTierInfo(currentTier);

  const orgCountryConfig = findCountry(org?.country);
  const orgSectorOptions = orgCountryConfig?.sectors ?? [];
  const orgRegionOptions = orgCountryConfig?.regions ?? [];
  const orgCountryName = org?.country || "";

  return (
    <div className="min-h-screen app-bg">
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
      <ErrorModal message={errorModal} onClose={() => setErrorModal(null)} />
      <h1 className="text-2xl font-bold text-stone-800">Settings</h1>

      {/* Org Profile */}
      <div className="card-gradient border border-stone-200 p-5">
        <h2 className="text-lg font-bold text-stone-700 mb-3">Organisation</h2>
        <div className="space-y-2 text-base">
          {/* Name */}
          <div className="flex justify-between items-center">
            <span className="text-stone-700">Name</span>
            {editing === "name" ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="px-2 py-1 text-base border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                  autoFocus
                />
                <button onClick={saveEdit} disabled={saving} className="text-sm text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                  {saving ? "..." : "Save"}
                </button>
                <button onClick={cancelEdit} className="text-sm text-stone-600 hover:text-stone-600">Cancel</button>
              </div>
            ) : (
              <button onClick={() => startEdit("name")} className="text-stone-800 hover:text-blue-600 transition-colors">
                {org?.name || "-"}
              </button>
            )}
          </div>

          {/* Country */}
          <div className="flex justify-between items-center">
            <span className="text-stone-700">Country</span>
            {editing === "country" ? (
              <div className="flex items-center gap-2">
                <select
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="px-2 py-1 text-base border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                  autoFocus
                >
                  {getEnabledCountries().map((c) => (
                    <option key={c.slug} value={c.name}>{c.name}</option>
                  ))}
                </select>
                <button onClick={saveEdit} disabled={saving} className="text-sm text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                  {saving ? "..." : "Save"}
                </button>
                <button onClick={cancelEdit} className="text-sm text-stone-600 hover:text-stone-600">Cancel</button>
              </div>
            ) : (
              <button onClick={() => startEdit("country")} className="text-stone-800 hover:text-blue-600 transition-colors">
                {org?.country || "-"}
              </button>
            )}
          </div>

          {/* Website */}
          <div className="flex justify-between items-center">
            <span className="text-stone-700">Website</span>
            {editing === "website" ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="px-2 py-1 text-base border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                  placeholder="https://..."
                  autoFocus
                />
                <button onClick={saveEdit} disabled={saving} className="text-sm text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                  {saving ? "..." : "Save"}
                </button>
                <button onClick={cancelEdit} className="text-sm text-stone-600 hover:text-stone-600">Cancel</button>
              </div>
            ) : (
              <button onClick={() => startEdit("website")} className="text-stone-800 hover:text-blue-600 transition-colors">
                {org?.website_url || org?.website || "-"}
              </button>
            )}
          </div>

          {/* Sectors */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-stone-700">Sectors</span>
              {editing !== "sectors" && (
                <button onClick={() => startEdit("sectors")} className="text-sm text-blue-600 hover:text-blue-700 font-medium">
                  Edit
                </button>
              )}
            </div>
            {editing === "sectors" ? (
              <div>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {orgSectorOptions.map((s) => (
                    <button
                      key={s}
                      onClick={() => toggleEditSector(s)}
                      className={`px-2.5 py-1 text-sm rounded-full border transition-colors ${
                        editSectors.includes(s)
                          ? "bg-blue-50 border-blue-300 text-blue-700"
                          : "bg-white border-stone-200 text-stone-600 hover:border-stone-300"
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <button onClick={saveEdit} disabled={saving} className="text-sm text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button onClick={cancelEdit} className="text-sm text-stone-600 hover:text-stone-600">Cancel</button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-1">
                {org?.sectors && org.sectors.length > 0 ? org.sectors.map((s) => (
                  <span key={s} className="text-sm bg-stone-100 text-stone-600 px-2 py-0.5 rounded-full">{s}</span>
                )) : <span className="text-sm text-stone-600">-</span>}
              </div>
            )}
          </div>

          {/* Geographies */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-stone-700">Geographies</span>
              {editing !== "geographies" && (
                <button onClick={() => startEdit("geographies")} className="text-sm text-blue-600 hover:text-blue-700 font-medium">
                  Edit
                </button>
              )}
            </div>
            {editing === "geographies" ? (
              <div>
                <div className="flex flex-col gap-2 mb-2">
                  {(() => {
                    const hasRegions = orgRegionOptions.length > 0;
                    const isWholeSelected = editGeographies.includes(orgCountryName);
                    const hasAnyRegionSelected = hasRegions && orgRegionOptions.some(
                      (r) => editGeographies.includes(`${orgCountryName} > ${r}`)
                    );

                    if (!orgCountryName) {
                      return <span className="text-sm text-stone-600">Set a country first.</span>;
                    }

                    return (
                      <div>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() =>
                              hasRegions
                                ? toggleEditWholeCountry(orgCountryName, orgRegionOptions)
                                : toggleEditGeo(orgCountryName)
                            }
                            className={`px-2.5 py-1 text-sm rounded-full border transition-colors ${
                              isWholeSelected
                                ? "bg-blue-50 border-blue-300 text-blue-700"
                                : hasAnyRegionSelected
                                  ? "bg-blue-50/50 border-blue-200 text-blue-600"
                                  : "bg-white border-stone-200 text-stone-600 hover:border-stone-300"
                            }`}
                          >
                            All of {orgCountryName}
                          </button>
                        </div>
                        {hasRegions && (
                          <div className="flex flex-wrap gap-1 mt-1 ml-4">
                            {orgRegionOptions.map((region) => (
                              <button
                                key={region}
                                onClick={() => toggleEditRegion(orgCountryName, region, orgRegionOptions)}
                                className={`px-2 py-0.5 text-sm rounded-full border transition-colors ${
                                  isEditRegionSelected(orgCountryName, region)
                                    ? "bg-blue-50 border-blue-300 text-blue-700"
                                    : "bg-white border-stone-200 text-stone-600 hover:border-stone-300"
                                }`}
                              >
                                {region}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>
                <div className="flex gap-2">
                  <button onClick={saveEdit} disabled={saving} className="text-sm text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button onClick={cancelEdit} className="text-sm text-stone-600 hover:text-stone-600">Cancel</button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-1">
                {org?.geographies && org.geographies.length > 0 ? org.geographies.map((g) => (
                  <span key={g} className="text-sm bg-stone-100 text-stone-600 px-2 py-0.5 rounded-full">
                    {g.includes(" > ") ? (
                      <>
                        {g.split(" > ")[0]}
                        <span className="text-stone-300 mx-0.5">&rsaquo;</span>
                        {g.split(" > ")[1]}
                      </>
                    ) : g}
                  </span>
                )) : <span className="text-sm text-stone-600">-</span>}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Organisation Data */}
      <div className="card-gradient border border-stone-200 p-5">
        <h2 className="text-lg font-bold text-stone-700 mb-1">Organisation Data</h2>
        <p className="text-sm text-stone-600 mb-4">
          Upload documents that inform your grant scanning cycles. These are used as context when identifying and assessing opportunities.
        </p>

        {/* Upload */}
        <div className="flex items-center gap-2 mb-4">
          <select
            value={uploadDocType}
            onChange={(e) => setUploadDocType(e.target.value)}
            className="px-2 py-1.5 text-sm border border-stone-200 rounded-md focus:outline-none focus:border-blue-400"
          >
            <option value="general">General</option>
            <option value="annual-reports">Annual Reports</option>
            <option value="mission-statements">Mission Statements</option>
            <option value="organisational-reviews">Organisational Reviews</option>
            <option value="previous-applications">Previous Applications</option>
            <option value="financial-statements">Financial Statements</option>
          </select>
          <label className={`px-3 py-1.5 text-sm rounded-md cursor-pointer transition-colors ${
            uploading ? "bg-stone-100 text-stone-600" : "bg-blue-600 text-white hover:bg-blue-700"
          }`}>
            {uploading ? "Uploading..." : "Upload file"}
            <input
              type="file"
              className="hidden"
              onChange={handleUploadFile}
              disabled={uploading}
              accept=".pdf,.docx,.doc,.xlsx,.xls,.txt,.md,.csv"
            />
          </label>
        </div>

        {/* File list */}
        {uploads.length === 0 ? (
          <p className="text-sm text-stone-600">No documents uploaded yet.</p>
        ) : (
          <div className="space-y-1.5">
            {uploads.map((file) => (
              <div key={file.filename} className="flex items-center justify-between py-1.5 px-3 bg-stone-50 rounded-md">
                <div className="flex items-center gap-2 min-w-0">
                  <svg className="w-3.5 h-3.5 text-stone-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                  </svg>
                  <span className="text-sm text-stone-600 truncate">{file.filename}</span>
                  <span className="text-sm text-stone-600 shrink-0">
                    {file.size > 1024 * 1024
                      ? `${(file.size / 1024 / 1024).toFixed(1)} MB`
                      : `${Math.round(file.size / 1024)} KB`}
                  </span>
                </div>
                <button
                  onClick={() => handleDeleteFile(file.filename)}
                  disabled={deletingFile === file.filename}
                  className="text-sm text-red-400 hover:text-red-600 transition-colors disabled:opacity-50 shrink-0 ml-2"
                >
                  {deletingFile === file.filename ? "..." : "Remove"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Tier */}
      <div className="card-gradient border border-stone-200 p-5">
        <h2 className="text-lg font-bold text-stone-700 mb-3">Service Tier</h2>

        {checkoutMessage && (
          <div className="mb-4 px-3 py-2 bg-green-50 border border-green-200 rounded-md text-sm text-green-700">
            {checkoutMessage}
          </div>
        )}

        {(() => {
          const scannerInfo = getTierInfo("scanner");
          const officerInfo = getTierInfo("officer");
          return currentTier === "scanner" ? (
          <>
            {/* Current plan */}
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base font-medium text-stone-800">{scannerInfo.label}</span>
                <span className="text-sm bg-stone-100 text-stone-700 px-2 py-0.5 rounded-full">Current</span>
              </div>
              <ul className="space-y-1 mb-4">
                {scannerInfo.features.map((f) => (
                  <li key={f} className="text-sm text-stone-700 flex items-center gap-1.5">
                    <span className="text-green-500">&#10003;</span> {f}
                  </li>
                ))}
              </ul>
            </div>

            {/* Upgrade card */}
            <div className="border border-blue-200 bg-blue-50/50 rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-base font-medium text-blue-800">{officerInfo.label}</span>
                <span className="text-base font-bold text-blue-700">{officerInfo.price}</span>
              </div>
              <ul className="space-y-1 mb-3">
                {officerInfo.features.map((f) => (
                  <li key={f} className="text-sm text-blue-700 flex items-center gap-1.5">
                    <span className="text-blue-500">&#10003;</span> {f}
                  </li>
                ))}
              </ul>
              <button
                onClick={handleUpgrade}
                disabled={checkingOut}
                className="w-full px-4 py-2.5 text-base font-medium btn-gradient rounded-md disabled:opacity-50"
              >
                {checkingOut ? "Redirecting to checkout..." : `Upgrade to ${officerInfo.label}`}
              </button>
            </div>
          </>
        ) : (
          <>
            {/* Active plan */}
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base font-medium text-blue-700">{officerInfo.label}</span>
                <span className="text-sm bg-blue-100 text-blue-600 px-2 py-0.5 rounded-full">Active</span>
              </div>
              <span className="text-sm text-stone-700">{officerInfo.price}</span>
              <ul className="space-y-1 mt-2">
                {officerInfo.features.map((f) => (
                  <li key={f} className="text-sm text-stone-700 flex items-center gap-1.5">
                    <span className="text-green-500">&#10003;</span> {f}
                  </li>
                ))}
              </ul>
            </div>

            <button
              onClick={handleManageBilling}
              className="px-4 py-2 text-base font-medium text-stone-700 bg-white border border-stone-200 hover:border-stone-300 rounded-md transition-colors"
            >
              Manage billing
            </button>
          </>
        );
        })()}
      </div>

      {/* Tailored Opportunities */}
      <div className="card-gradient border border-stone-200 p-5">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div className="flex-1">
            <h2 className="text-lg font-bold text-stone-700">Tailored Opportunities</h2>
            <p className="text-sm text-stone-700 mt-1">
              On top of the always-on search box, get a weekly profile-steered grant scan
              with priority-tagged opportunities matched to your sectors and geographies.
            </p>
          </div>
          {currentTier === "officer" ? (
            <button
              onClick={() => handleToggleTailored(!tailored?.tailored_enabled)}
              disabled={savingTailored}
              className={`shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50 ${
                tailored?.tailored_enabled ? "bg-blue-600" : "bg-stone-300"
              }`}
              aria-label="Toggle Tailored Opportunities"
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  tailored?.tailored_enabled ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          ) : (
            <span className="shrink-0 text-sm px-2 py-1 bg-blue-50 text-blue-700 border border-blue-200 rounded-full">
              Officer-tier
            </span>
          )}
        </div>

        {currentTier === "officer" ? (
          <div className="text-sm text-stone-700">
            {tailored?.tailored_enabled ? (
              tailored.timer && !tailored.timer.expired ? (
                <span>
                  ● Cooldown — next cycle available in <strong>{formatRemaining(tailored.timer.remaining_seconds)}</strong>.
                </span>
              ) : (
                <span>● Ready — head to the &quot;Tailored Picks&quot; tab on the dashboard to run a cycle.</span>
              )
            ) : (
              <span className="text-stone-600">Off — toggle on to start running weekly tailored cycles.</span>
            )}
          </div>
        ) : (
          <div className="text-sm text-stone-700">
            Available on <strong>Grant Officer</strong>. Upgrade above to unlock weekly tailored cycles.
          </div>
        )}
      </div>

      {/* Privacy & Data Policy */}
      <div className="card-gradient border border-stone-200 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-stone-700">Privacy & Data Policy</h2>
            <p className="text-sm text-stone-600 mt-0.5">How we collect, use, and protect your data.</p>
          </div>
          <a
            href="/privacy"
            className="px-4 py-2 text-base font-medium text-stone-700 bg-white border border-stone-200 hover:border-stone-300 rounded-md transition-colors"
          >
            View policy
          </a>
        </div>
      </div>
    </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <AuthGate>
      <SettingsContent />
    </AuthGate>
  );
}
