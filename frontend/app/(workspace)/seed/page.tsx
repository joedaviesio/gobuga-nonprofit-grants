"use client";

// The one sign-up screen: register → here → the feed. Everything on it is
// optional. Whatever the person fills in goes to POST /api/org/setup (fields
// left empty are not sent, so they keep their current value), then
// POST /api/org/seeding-complete finishes onboarding. "Skip for now" does the
// same with nothing filled in, and lands on a working, unranked feed.
//
// What each field feeds (so it is worth asking for):
//   sectors, regions, status, income, need
//               → rank the grant list ("Best fit", api/org_fit.py)
//   website     → scraped into the org's data for the bots
//   documents   → the org's data for the bots

import { useState, useEffect, useRef } from "react";
import FitFields, { EMPTY_FIT, useFitTaxonomy, type FitValues } from "@/app/fit-fields";
import {
  uploadOrgDocument,
  listOrgUploads,
  completeSeedingStep,
  getOrgProfile,
  getToken,
  setupOrg,
  verifySession,
  type OrgSetupData,
} from "@/lib/api";
import { getEnabledCountries, loadDeploymentConfig } from "@/lib/countries";
import { onboardingRedirect } from "@/lib/onboarding";
import LoadingBar from "@/app/loading-bar";
import { useI18n, type MessageKey } from "@/lib/i18n";

const DOC_TYPES: { key: string; labelKey: MessageKey }[] = [
  { key: "general", labelKey: "seed.doc_general" },
  { key: "annual-reports", labelKey: "seed.doc_annual_reports" },
  { key: "mission-statements", labelKey: "seed.doc_mission_statements" },
  { key: "organisational-reviews", labelKey: "seed.doc_org_reviews" },
  { key: "previous-applications", labelKey: "seed.doc_previous_applications" },
  { key: "financial-statements", labelKey: "seed.doc_financial_statements" },
];

function websiteIsValid(v: string): boolean {
  const withScheme = v.match(/^https?:\/\//) ? v : `https://${v}`;
  try {
    return new URL(withScheme).hostname.includes(".");
  } catch {
    return false;
  }
}

function chipClass(selected: boolean): string {
  return `px-3 py-1.5 text-sm rounded-full border transition-colors ${
    selected
      ? "bg-blue-50 border-blue-300 text-blue-700 shadow-sm"
      : "bg-white border-stone-200 text-stone-700 hover:border-blue-300 hover:bg-blue-50/30"
  }`;
}

function Section({ label, help, children }: { label: string; help: string; children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="card-gradient border border-stone-200/60 backdrop-blur-sm p-5 shadow-sm">
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <h2 className="text-base font-semibold text-stone-800">{label}</h2>
        <span className="text-xs text-stone-500 shrink-0">{t("seed.optional")}</span>
      </div>
      <p className="text-sm text-stone-600 mb-3">{help}</p>
      {children}
    </div>
  );
}

export default function SeedPage() {
  const { t } = useI18n();
  const [ready, setReady] = useState(false);
  const [countryName, setCountryName] = useState("");
  const [sectorOptions, setSectorOptions] = useState<string[]>([]);
  const [regionOptions, setRegionOptions] = useState<string[]>([]);

  const [sectors, setSectors] = useState<string[]>([]);
  const [geographies, setGeographies] = useState<string[]>([]);
  const [fit, setFit] = useState<FitValues>(EMPTY_FIT);
  const fitTaxonomy = useFitTaxonomy();
  const [website, setWebsite] = useState("");
  const [websiteTouched, setWebsiteTouched] = useState(false);

  const [uploads, setUploads] = useState<Record<string, { filename: string; size: number } | null>>({});
  const [uploading, setUploading] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    verifySession().then(async (session) => {
      if (!session) {
        window.location.href = "/login";
        return;
      }
      const redirect = onboardingRedirect(session, "/seed");
      if (redirect) {
        window.location.href = redirect;
        return;
      }

      await loadDeploymentConfig();
      const country = getEnabledCountries()[0];
      setCountryName(country?.name ?? "");
      setSectorOptions(country?.sectors ?? []);
      setRegionOptions(country?.regions ?? []);

      // Prefill for an account part-way through the old two-step flow
      const org = await getOrgProfile().catch(() => null);
      if (org) {
        setSectors(org.sectors ?? []);
        setGeographies(org.geographies ?? []);
        setFit({ fit_status: org.fit_status ?? "", fit_size: org.fit_size ?? "", fit_need: org.fit_need ?? "" });
        setWebsite(org.website_url || org.website || "");
      }
      setReady(true);
    });

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

  const toggleSector = (s: string) => {
    setSectors((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  };

  // Regions are stored as "<Country> > <Region>", or "<Country>" for the whole
  // country, the same shape settings edits.
  const toggleWholeCountry = () => {
    setGeographies((prev) => {
      const withoutCountry = prev.filter((g) => !g.startsWith(`${countryName} > `) && g !== countryName);
      return prev.includes(countryName) ? withoutCountry : [...withoutCountry, countryName];
    });
  };

  const toggleRegion = (region: string) => {
    const prefixed = `${countryName} > ${region}`;
    setGeographies((prev) => {
      if (prev.includes(countryName)) {
        // Whole country was selected: switch to every region except this one
        const rest = regionOptions.filter((r) => r !== region).map((r) => `${countryName} > ${r}`);
        return [...prev.filter((g) => g !== countryName), ...rest];
      }
      if (prev.includes(prefixed)) return prev.filter((g) => g !== prefixed);
      const next = [...prev, prefixed];
      const selected = next.filter((g) => g.startsWith(`${countryName} > `));
      if (selected.length === regionOptions.length) {
        return [...next.filter((g) => !g.startsWith(`${countryName} > `)), countryName];
      }
      return next;
    });
  };

  const regionSelected = (region: string) =>
    geographies.includes(countryName) || geographies.includes(`${countryName} > ${region}`);

  const handleUpload = async (docType: string, file: File) => {
    setError("");
    setUploading(docType);
    try {
      const result = await uploadOrgDocument(file, docType);
      setUploads((prev) => ({ ...prev, [docType]: { filename: result.filename, size: result.size } }));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errors.upload"));
    } finally {
      setUploading(null);
    }
  };

  const websiteError = website.trim() !== "" && !websiteIsValid(website.trim());

  const finish = async (skip: boolean) => {
    if (!skip && websiteError) {
      setWebsiteTouched(true);
      return;
    }
    setError("");
    setSaving(true);
    // The deployment's country, as the old setup screen stored it; not a question
    const data: OrgSetupData = countryName ? { country: countryName } : {};
    if (!skip) {
      if (sectors.length) data.sectors = sectors;
      if (geographies.length) data.geographies = geographies;
      if (fit.fit_status) data.fit_status = fit.fit_status;
      if (fit.fit_size) data.fit_size = fit.fit_size;
      if (fit.fit_need) data.fit_need = fit.fit_need;
      if (website.trim()) data.website = website.trim();
    }
    try {
      await setupOrg(data);
      await completeSeedingStep();
      window.location.href = "/workspace";
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errors.save"));
      setSaving(false);
    }
  };

  if (!ready) {
    return (
      <div className="min-h-screen auth-bg flex items-center justify-center">
        <div className="w-48">
          <LoadingBar />
        </div>
      </div>
    );
  }

  const wholeCountrySelected = geographies.includes(countryName);

  return (
    <div className="min-h-screen auth-bg py-12 relative overflow-hidden">
      <div className="orb orb-yellow" style={{ top: "10%", right: "5%" }} />
      <div className="orb orb-blue" style={{ bottom: "15%", left: "10%" }} />

      <div className="max-w-lg mx-auto px-4 relative z-10 space-y-4">
        <div className="text-center mb-4">
          <h1 className="text-2xl font-bold text-stone-900">{t("seed.title")}</h1>
          <p className="text-base text-stone-700 mt-1">{t("seed.subtitle")}</p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-base text-red-700">{error}</div>
        )}

        {sectorOptions.length > 0 && (
          <Section label={t("seed.sectors_label")} help={t("seed.sectors_help")}>
            <div className="flex flex-wrap gap-2">
              {sectorOptions.map((s) => (
                <button key={s} type="button" onClick={() => toggleSector(s)} className={chipClass(sectors.includes(s))}>
                  {s}
                </button>
              ))}
            </div>
          </Section>
        )}

        {countryName && (
          <Section label={t("seed.regions_label")} help={t("seed.regions_help")}>
            <div className="flex flex-wrap gap-1.5">
              <button type="button" onClick={toggleWholeCountry} className={chipClass(wholeCountrySelected)}>
                {t("setup.all_of", { country: countryName })}
              </button>
              {regionOptions.map((region) => (
                <button key={region} type="button" onClick={() => toggleRegion(region)} className={chipClass(regionSelected(region))}>
                  {region}
                </button>
              ))}
            </div>
          </Section>
        )}

        {fitTaxonomy && (
          <Section label={t("seed.fit_label")} help={t("seed.fit_help")}>
            <FitFields
              values={fit}
              onChange={setFit}
              taxonomy={fitTaxonomy}
              selectClassName="w-full px-3 py-2 text-base border border-stone-300 rounded-md bg-white text-stone-900 focus:outline-none focus:border-blue-400"
            />
          </Section>
        )}

        <Section label={t("auth.website_url")} help={t("seed.website_help")}>
          <input
            type="text"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
            onBlur={() => setWebsiteTouched(true)}
            className={`w-full px-3 py-2 text-base border rounded-md focus:outline-none text-stone-900 ${
              websiteTouched && websiteError ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"
            }`}
            placeholder="yourorg.com"
          />
          {websiteTouched && websiteError && (
            <p className="text-sm text-red-600 mt-1">{t("auth.url_invalid")}</p>
          )}
        </Section>

        <Section label={t("seed.documents_label")} help={t("seed.documents_help")}>
          <div className="space-y-2">
            {DOC_TYPES.map((dt) => {
              const uploaded = uploads[dt.key];
              const isUploading = uploading === dt.key;
              return (
                <div key={dt.key} className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <p className="text-base text-stone-800">{t(dt.labelKey)}</p>
                    {uploaded && (
                      <p className="text-sm text-stone-600 truncate">
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
                      type="button"
                      onClick={() => fileInputRefs.current[dt.key]?.click()}
                      className={`ml-3 px-3 py-1 text-sm rounded-md border transition-colors ${
                        uploaded
                          ? "bg-green-50 border-green-300 text-green-700 font-medium"
                          : "bg-white border-stone-300 text-stone-700 hover:border-blue-300 hover:bg-blue-50/30"
                      }`}
                    >
                      {uploaded ? t("seed.replace") : t("seed.upload")}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </Section>

        <div className="pt-2 flex gap-3">
          <button
            type="button"
            onClick={() => finish(true)}
            disabled={saving || uploading !== null}
            className="px-4 py-2.5 text-base border border-stone-300 text-stone-700 rounded-md hover:bg-stone-100 transition-colors disabled:opacity-50"
          >
            {t("seed.skip_for_now")}
          </button>
          <button
            type="button"
            onClick={() => finish(false)}
            disabled={saving || uploading !== null}
            className="flex-1 px-4 py-2.5 text-base btn-gradient rounded-md disabled:opacity-50"
          >
            {saving ? t("common.saving") : t("seed.save_and_continue")}
          </button>
        </div>
      </div>
    </div>
  );
}
