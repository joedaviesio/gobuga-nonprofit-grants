"use client";

import { useI18n, type MessageKey } from "@/lib/i18n";
import { TERMS_ENABLED } from "@/lib/terms";

// Section keys in display order. Text lives in the locale catalogs
// (terms.<section>_title / _body) so the approved wording can land as a
// locale-only change.
const SECTIONS = [
  "parties",
  "service",
  "accounts",
  "fees",
  "data",
  "acceptable_use",
  "termination",
  "liability",
  "changes",
  "contact",
] as const;

export default function TermsPage() {
  const { t } = useI18n();
  return (
    <div className="min-h-screen app-bg">
      <div className="max-w-2xl mx-auto px-6 py-8">
        <h1 className="text-2xl font-bold text-stone-900 mb-6">{t("terms.title")}</h1>

        <div className="card-gradient border border-stone-200 p-6 space-y-6">
          {!TERMS_ENABLED ? (
            <p className="text-base text-stone-700 leading-relaxed">
              {t("terms.pending_body")}{" "}
              <a href="mailto:privacy@gobuga.org" className="text-blue-600 underline hover:text-blue-800">privacy@gobuga.org</a>.
            </p>
          ) : (
            <>
              <p className="text-base text-stone-700 leading-relaxed">{t("terms.intro")}</p>

              {SECTIONS.map((s) => (
                <div key={s}>
                  <h2 className="text-lg font-bold text-stone-800 mb-1">{t(`terms.${s}_title` as MessageKey)}</h2>
                  <p className="text-base text-stone-700 leading-relaxed">
                    {t(`terms.${s}_body` as MessageKey)}
                    {s === "data" && (
                      <>
                        {" "}
                        <a href="/privacy" className="text-blue-600 underline hover:text-blue-800">{t("privacy.title")}</a>.
                      </>
                    )}
                    {s === "contact" && (
                      <>
                        {" "}
                        <a href="mailto:privacy@gobuga.org" className="text-blue-600 underline hover:text-blue-800">privacy@gobuga.org</a>.
                      </>
                    )}
                  </p>
                </div>
              ))}

              <p className="text-sm text-stone-600 pt-2 border-t border-stone-100">
                {t("terms.footer")}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
