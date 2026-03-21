"use client";

import { useEffect, useState } from "react";
import { getOrgProfile, getOrgUsage, createCheckout, getBillingPortal, updateOrgProfile, toggleTier, listOrgUploads, uploadOrgDocument, deleteOrgUpload, type OrgProfile } from "@/lib/api";
import { GEOGRAPHIES } from "@/lib/geographies";
import { SECTORS } from "@/lib/sectors";
import LoadingBar from "@/app/loading-bar";
import AuthGate, { useAuth } from "../auth-gate";

const TIER_INFO = {
  scanner: {
    label: "Grant Scanner",
    price: "Free",
    features: ["1 cycle per week", "5 opportunities per cycle", "1 open case at a time", "5 chat messages/case"],
  },
  officer: {
    label: "Grant Officer",
    price: "$49 NZD/mo (~$29 USD)",
    features: ["1 cycle per week", "All opportunities", "Unlimited open cases", "Unlimited chat", "DOCX export", "Parse & fill", "Premium models"],
  },
};

function SettingsContent() {
  const { session, refreshSession } = useAuth();
  const [org, setOrg] = useState<OrgProfile | null>(null);
  const [usage, setUsage] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [togglingTier, setTogglingTier] = useState(false);

  // Uploads state
  const [uploads, setUploads] = useState<{ filename: string; size: number }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [deletingFile, setDeletingFile] = useState<string | null>(null);
  const [uploadDocType, setUploadDocType] = useState("general");

  // Editing state
  const [editing, setEditing] = useState<string | null>(null); // "name" | "country" | "website" | "sectors" | "geographies"
  const [editValue, setEditValue] = useState("");
  const [editSectors, setEditSectors] = useState<string[]>([]);
  const [editGeographies, setEditGeographies] = useState<string[]>([]);
  const [expandedCountries, setExpandedCountries] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([
      getOrgProfile().catch(() => null),
      getOrgUsage().catch(() => null),
      listOrgUploads().catch(() => []),
    ]).then(([o, u, files]) => {
      setOrg(o);
      setUsage(u);
      setUploads(files);
      setLoading(false);
    });
  }, []);

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
    setExpandedCountries(new Set());
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
      alert(err instanceof Error ? err.message : "Failed to save");
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

  const toggleExpand = (country: string) => {
    setExpandedCountries((prev) => {
      const next = new Set(prev);
      if (next.has(country)) next.delete(country);
      else next.add(country);
      return next;
    });
  };

  const handleToggleTier = async () => {
    setTogglingTier(true);
    try {
      await toggleTier();
      await refreshSession();
      const fresh = await getOrgProfile().catch(() => null);
      if (fresh) setOrg(fresh);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to toggle tier");
    } finally {
      setTogglingTier(false);
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
      alert(err instanceof Error ? err.message : "Upload failed");
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
      alert(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeletingFile(null);
    }
  };

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-8">
        <LoadingBar label="Loading settings..." />
      </div>
    );
  }

  const currentTier = session?.tier || "scanner";
  const tierInfo = TIER_INFO[currentTier] || TIER_INFO.scanner;

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
      <h1 className="text-lg font-bold text-stone-800">Settings</h1>

      {/* Org Profile */}
      <div className="bg-white border border-stone-200 rounded-lg p-5">
        <h2 className="text-sm font-bold text-stone-700 mb-3">Organisation</h2>
        <div className="space-y-2 text-sm">
          {/* Name */}
          <div className="flex justify-between items-center">
            <span className="text-stone-500">Name</span>
            {editing === "name" ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="px-2 py-1 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                  autoFocus
                />
                <button onClick={saveEdit} disabled={saving} className="text-xs text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                  {saving ? "..." : "Save"}
                </button>
                <button onClick={cancelEdit} className="text-xs text-stone-400 hover:text-stone-600">Cancel</button>
              </div>
            ) : (
              <button onClick={() => startEdit("name")} className="text-stone-800 hover:text-blue-600 transition-colors">
                {org?.name || "-"}
              </button>
            )}
          </div>

          {/* Country */}
          <div className="flex justify-between items-center">
            <span className="text-stone-500">Country</span>
            {editing === "country" ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="px-2 py-1 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                  autoFocus
                />
                <button onClick={saveEdit} disabled={saving} className="text-xs text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                  {saving ? "..." : "Save"}
                </button>
                <button onClick={cancelEdit} className="text-xs text-stone-400 hover:text-stone-600">Cancel</button>
              </div>
            ) : (
              <button onClick={() => startEdit("country")} className="text-stone-800 hover:text-blue-600 transition-colors">
                {org?.country || "-"}
              </button>
            )}
          </div>

          {/* Website */}
          <div className="flex justify-between items-center">
            <span className="text-stone-500">Website</span>
            {editing === "website" ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="px-2 py-1 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                  placeholder="https://..."
                  autoFocus
                />
                <button onClick={saveEdit} disabled={saving} className="text-xs text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                  {saving ? "..." : "Save"}
                </button>
                <button onClick={cancelEdit} className="text-xs text-stone-400 hover:text-stone-600">Cancel</button>
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
              <span className="text-stone-500">Sectors</span>
              {editing !== "sectors" && (
                <button onClick={() => startEdit("sectors")} className="text-xs text-blue-600 hover:text-blue-700 font-medium">
                  Edit
                </button>
              )}
            </div>
            {editing === "sectors" ? (
              <div>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {SECTORS.map((s) => (
                    <button
                      key={s}
                      onClick={() => toggleEditSector(s)}
                      className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                        editSectors.includes(s)
                          ? "bg-blue-50 border-blue-300 text-blue-700"
                          : "bg-white border-stone-200 text-stone-400 hover:border-stone-300"
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <button onClick={saveEdit} disabled={saving} className="text-xs text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button onClick={cancelEdit} className="text-xs text-stone-400 hover:text-stone-600">Cancel</button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-1">
                {org?.sectors && org.sectors.length > 0 ? org.sectors.map((s) => (
                  <span key={s} className="text-xs bg-stone-100 text-stone-600 px-2 py-0.5 rounded-full">{s}</span>
                )) : <span className="text-xs text-stone-400">-</span>}
              </div>
            )}
          </div>

          {/* Geographies */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-stone-500">Geographies</span>
              {editing !== "geographies" && (
                <button onClick={() => startEdit("geographies")} className="text-xs text-blue-600 hover:text-blue-700 font-medium">
                  Edit
                </button>
              )}
            </div>
            {editing === "geographies" ? (
              <div>
                <div className="flex flex-col gap-2 mb-2">
                  {GEOGRAPHIES.map((geo) => {
                    const hasRegions = geo.regions && geo.regions.length > 0;
                    const isExpanded = expandedCountries.has(geo.name);
                    const isWholeSelected = editGeographies.includes(geo.name);
                    const hasAnyRegionSelected = hasRegions && geo.regions!.some(
                      (r) => editGeographies.includes(`${geo.name} > ${r}`)
                    );

                    return (
                      <div key={geo.name}>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() =>
                              hasRegions
                                ? toggleEditWholeCountry(geo.name, geo.regions!)
                                : toggleEditGeo(geo.name)
                            }
                            className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                              isWholeSelected || (!hasRegions && editGeographies.includes(geo.name))
                                ? "bg-blue-50 border-blue-300 text-blue-700"
                                : hasAnyRegionSelected
                                  ? "bg-blue-50/50 border-blue-200 text-blue-600"
                                  : "bg-white border-stone-200 text-stone-400 hover:border-stone-300"
                            }`}
                          >
                            {geo.name}
                          </button>
                          {hasRegions && (
                            <button
                              onClick={() => toggleExpand(geo.name)}
                              className="p-0.5 text-stone-400 hover:text-stone-600 transition-colors"
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
                            </button>
                          )}
                        </div>
                        {hasRegions && isExpanded && (
                          <div className="flex flex-wrap gap-1 mt-1 ml-4">
                            {geo.regions!.map((region) => (
                              <button
                                key={region}
                                onClick={() => toggleEditRegion(geo.name, region, geo.regions!)}
                                className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${
                                  isEditRegionSelected(geo.name, region)
                                    ? "bg-blue-50 border-blue-300 text-blue-700"
                                    : "bg-white border-stone-200 text-stone-400 hover:border-stone-300"
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
                <div className="flex gap-2">
                  <button onClick={saveEdit} disabled={saving} className="text-xs text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50">
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button onClick={cancelEdit} className="text-xs text-stone-400 hover:text-stone-600">Cancel</button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-1">
                {org?.geographies && org.geographies.length > 0 ? org.geographies.map((g) => (
                  <span key={g} className="text-xs bg-stone-100 text-stone-600 px-2 py-0.5 rounded-full">
                    {g.includes(" > ") ? (
                      <>
                        {g.split(" > ")[0]}
                        <span className="text-stone-300 mx-0.5">&rsaquo;</span>
                        {g.split(" > ")[1]}
                      </>
                    ) : g}
                  </span>
                )) : <span className="text-xs text-stone-400">-</span>}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Organisation Data */}
      <div className="bg-white border border-stone-200 rounded-lg p-5">
        <h2 className="text-sm font-bold text-stone-700 mb-1">Organisation Data</h2>
        <p className="text-xs text-stone-400 mb-4">
          Upload documents that inform your grant scanning cycles. These are used as context when identifying and assessing opportunities.
        </p>

        {/* Upload */}
        <div className="flex items-center gap-2 mb-4">
          <select
            value={uploadDocType}
            onChange={(e) => setUploadDocType(e.target.value)}
            className="px-2 py-1.5 text-xs border border-stone-200 rounded-md focus:outline-none focus:border-blue-400"
          >
            <option value="general">General</option>
            <option value="annual-reports">Annual Reports</option>
            <option value="mission-statements">Mission Statements</option>
            <option value="organisational-reviews">Organisational Reviews</option>
            <option value="previous-applications">Previous Applications</option>
            <option value="financial-statements">Financial Statements</option>
          </select>
          <label className={`px-3 py-1.5 text-xs rounded-md cursor-pointer transition-colors ${
            uploading ? "bg-stone-100 text-stone-400" : "bg-blue-600 text-white hover:bg-blue-700"
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
          <p className="text-xs text-stone-400">No documents uploaded yet.</p>
        ) : (
          <div className="space-y-1.5">
            {uploads.map((file) => (
              <div key={file.filename} className="flex items-center justify-between py-1.5 px-3 bg-stone-50 rounded-md">
                <div className="flex items-center gap-2 min-w-0">
                  <svg className="w-3.5 h-3.5 text-stone-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                  </svg>
                  <span className="text-xs text-stone-600 truncate">{file.filename}</span>
                  <span className="text-xs text-stone-400 shrink-0">
                    {file.size > 1024 * 1024
                      ? `${(file.size / 1024 / 1024).toFixed(1)} MB`
                      : `${Math.round(file.size / 1024)} KB`}
                  </span>
                </div>
                <button
                  onClick={() => handleDeleteFile(file.filename)}
                  disabled={deletingFile === file.filename}
                  className="text-xs text-red-400 hover:text-red-600 transition-colors disabled:opacity-50 shrink-0 ml-2"
                >
                  {deletingFile === file.filename ? "..." : "Remove"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Tier */}
      <div className="bg-white border border-stone-200 rounded-lg p-5">
        <h2 className="text-sm font-bold text-stone-700 mb-3">Service Tier</h2>

        {/* Toggle switch */}
        <div className="flex items-center gap-4 mb-4">
          <div className="flex items-center gap-3">
            <span className={`text-sm font-medium ${currentTier === "scanner" ? "text-stone-800" : "text-stone-400"}`}>
              Scanner
            </span>
            <button
              onClick={handleToggleTier}
              disabled={togglingTier}
              className={`relative w-12 h-6 rounded-full transition-colors ${
                currentTier === "officer" ? "bg-blue-600" : "bg-stone-300"
              }`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${
                  currentTier === "officer" ? "translate-x-6" : ""
                }`}
              />
            </button>
            <span className={`text-sm font-medium ${currentTier === "officer" ? "text-blue-600" : "text-stone-400"}`}>
              Officer
            </span>
          </div>
          <span className="text-xs text-stone-400">{tierInfo.price}</span>
        </div>

        <ul className="space-y-1 mb-4">
          {tierInfo.features.map((f) => (
            <li key={f} className="text-xs text-stone-500 flex items-center gap-1.5">
              <span className="text-green-500">&#10003;</span> {f}
            </li>
          ))}
        </ul>

        <p className="text-xs text-stone-400 italic">
          Dev toggle — payment integration coming soon.
        </p>
      </div>

      {/* Usage */}
      {usage && (
        <div className="bg-white border border-stone-200 rounded-lg p-5">
          <h2 className="text-sm font-bold text-stone-700 mb-3">Today's Usage</h2>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <div className="text-lg font-bold text-stone-800">{(usage as Record<string, number>).total_calls || 0}</div>
              <div className="text-xs text-stone-400">API Calls</div>
            </div>
            <div>
              <div className="text-lg font-bold text-stone-800">
                {(((usage as Record<string, number>).total_input_tokens || 0) / 1000).toFixed(1)}K
              </div>
              <div className="text-xs text-stone-400">Input Tokens</div>
            </div>
            <div>
              <div className="text-lg font-bold text-stone-800">
                {(((usage as Record<string, number>).total_output_tokens || 0) / 1000).toFixed(1)}K
              </div>
              <div className="text-xs text-stone-400">Output Tokens</div>
            </div>
          </div>
        </div>
      )}
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
