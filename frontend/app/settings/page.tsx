"use client";

import { useEffect, useState } from "react";
import { getOrgProfile, getOrgUsage, createCheckout, getBillingPortal, type OrgProfile } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import AuthGate, { useAuth } from "../auth-gate";

const PLAN_INFO = {
  free: { label: "Free", price: "$0/mo", features: ["2 scans/month", "1 active case", "5 chat messages/case"] },
  starter: { label: "Starter", price: "$49/mo", features: ["Daily scans", "5 active cases", "50 chat messages/case", "DOCX export", "Parse & fill"] },
  professional: { label: "Professional", price: "$149/mo", features: ["Daily scans", "Unlimited cases", "Unlimited chat", "DOCX export", "Parse & fill", "Premium models"] },
};

function SettingsContent() {
  const { session } = useAuth();
  const [org, setOrg] = useState<OrgProfile | null>(null);
  const [usage, setUsage] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [upgrading, setUpgrading] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      getOrgProfile().catch(() => null),
      getOrgUsage().catch(() => null),
    ]).then(([o, u]) => {
      setOrg(o);
      setUsage(u);
      setLoading(false);
    });
  }, []);

  const handleUpgrade = async (plan: string) => {
    setUpgrading(plan);
    try {
      const result = await createCheckout(plan);
      if (result.url) {
        window.location.href = result.url;
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create checkout");
    } finally {
      setUpgrading(null);
    }
  };

  const handleManageBilling = async () => {
    try {
      const result = await getBillingPortal();
      if (result.url) {
        window.location.href = result.url;
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to open billing portal");
    }
  };

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-8">
        <LoadingBar label="Loading settings..." />
      </div>
    );
  }

  const currentPlan = org?.plan || "free";
  const planInfo = PLAN_INFO[currentPlan as keyof typeof PLAN_INFO] || PLAN_INFO.free;

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
      <h1 className="text-lg font-bold text-stone-800">Settings</h1>

      {/* Org Profile */}
      <div className="bg-white border border-stone-200 rounded-lg p-5">
        <h2 className="text-sm font-bold text-stone-700 mb-3">Organisation</h2>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-stone-500">Name</span>
            <span className="text-stone-800">{org?.name || "-"}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-stone-500">Country</span>
            <span className="text-stone-800">{org?.country || "-"}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-stone-500">Website</span>
            <span className="text-stone-800">{org?.website || "-"}</span>
          </div>
          {org?.sectors && org.sectors.length > 0 && (
            <div>
              <span className="text-stone-500 block mb-1">Sectors</span>
              <div className="flex flex-wrap gap-1">
                {org.sectors.map((s) => (
                  <span key={s} className="text-xs bg-stone-100 text-stone-600 px-2 py-0.5 rounded-full">{s}</span>
                ))}
              </div>
            </div>
          )}
          {org?.geographies && org.geographies.length > 0 && (
            <div>
              <span className="text-stone-500 block mb-1">Geographies</span>
              <div className="flex flex-wrap gap-1">
                {org.geographies.map((g) => (
                  <span key={g} className="text-xs bg-stone-100 text-stone-600 px-2 py-0.5 rounded-full">{g}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Plan */}
      <div className="bg-white border border-stone-200 rounded-lg p-5">
        <h2 className="text-sm font-bold text-stone-700 mb-3">Plan</h2>
        <div className="flex items-center gap-3 mb-4">
          <span className="text-sm font-medium text-stone-800">{planInfo.label}</span>
          <span className="text-xs text-stone-400">{planInfo.price}</span>
        </div>
        <ul className="space-y-1 mb-4">
          {planInfo.features.map((f) => (
            <li key={f} className="text-xs text-stone-500 flex items-center gap-1.5">
              <span className="text-green-500">&#10003;</span> {f}
            </li>
          ))}
        </ul>

        {currentPlan === "free" && (
          <div className="flex gap-2">
            <button
              onClick={() => handleUpgrade("starter")}
              disabled={upgrading !== null}
              className="px-4 py-2 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {upgrading === "starter" ? "..." : "Upgrade to Starter ($49/mo)"}
            </button>
            <button
              onClick={() => handleUpgrade("professional")}
              disabled={upgrading !== null}
              className="px-4 py-2 text-xs bg-stone-800 text-white rounded-md hover:bg-stone-900 disabled:opacity-50"
            >
              {upgrading === "professional" ? "..." : "Upgrade to Professional ($149/mo)"}
            </button>
          </div>
        )}

        {currentPlan === "starter" && (
          <div className="flex gap-2">
            <button
              onClick={() => handleUpgrade("professional")}
              disabled={upgrading !== null}
              className="px-4 py-2 text-xs bg-stone-800 text-white rounded-md hover:bg-stone-900 disabled:opacity-50"
            >
              {upgrading === "professional" ? "..." : "Upgrade to Professional ($149/mo)"}
            </button>
            <button
              onClick={handleManageBilling}
              className="px-4 py-2 text-xs border border-stone-200 text-stone-600 rounded-md hover:bg-stone-50"
            >
              Manage billing
            </button>
          </div>
        )}

        {currentPlan === "professional" && (
          <button
            onClick={handleManageBilling}
            className="px-4 py-2 text-xs border border-stone-200 text-stone-600 rounded-md hover:bg-stone-50"
          >
            Manage billing
          </button>
        )}
      </div>

      {/* Usage */}
      {usage && (
        <div className="bg-white border border-stone-200 rounded-lg p-5">
          <h2 className="text-sm font-bold text-stone-700 mb-3">Today's Usage</h2>
          <div className="grid grid-cols-4 gap-4 text-center">
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
            <div>
              <div className="text-lg font-bold text-stone-800">
                ${((usage as Record<string, number>).total_cost_usd || 0).toFixed(2)}
              </div>
              <div className="text-xs text-stone-400">Cost (USD)</div>
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
