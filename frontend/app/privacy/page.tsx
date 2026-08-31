"use client";

import { useI18n } from "@/lib/i18n";

export default function PrivacyPage() {
  const { t } = useI18n();
  return (
    <div className="min-h-screen app-bg">
      <div className="max-w-2xl mx-auto px-6 py-8">
        <h1 className="text-2xl font-bold text-stone-900 mb-6">{t("privacy.title")}</h1>

        <div className="card-gradient border border-stone-200 p-6 space-y-6">
          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">{t("privacy.collect_title")}</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              {t("privacy.collect_body")}
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">{t("privacy.use_title")}</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              {t("privacy.use_body")}
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">{t("privacy.security_title")}</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              {t("privacy.security_body")}
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">{t("privacy.rights_title")}</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              {t("privacy.rights_body")}{" "}
              <a href="mailto:privacy@gobuga.org" className="text-blue-600 underline hover:text-blue-800">privacy@gobuga.org</a>.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">{t("privacy.retention_title")}</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              {t("privacy.retention_body")}
            </p>
          </div>

          <p className="text-sm text-stone-600 pt-2 border-t border-stone-100">
            {t("privacy.footer")} <a href="mailto:privacy@gobuga.org" className="text-blue-600 underline hover:text-blue-800">privacy@gobuga.org</a>.
            <br />{t("privacy.owned_by")} <a href="https://proxymatches.com" target="_blank" rel="noopener" className="text-blue-600 underline hover:text-blue-800">Proxy Matches</a>.
          </p>
        </div>
      </div>
    </div>
  );
}
