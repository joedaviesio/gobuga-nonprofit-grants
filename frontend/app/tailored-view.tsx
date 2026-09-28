"use client";

// Tailored Picks: the same open grants as the Opportunities tab and the
// public site, ranked against the organisation's profile by the public /fit
// scorer (GET /api/tailored). Instant and free; each grant says why it fits.

import { useEffect, useState, type ReactNode } from "react";
import { getTailored, type OpportunityRow, type TailoredParams, type TailoredResult } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import { fitLabel, useFitTaxonomy } from "@/app/fit-fields";
import { useI18n } from "@/lib/i18n";
import { regionLabel } from "@/lib/labels";

const PAGE = 50;

function ProfileSummary({ params }: { params: TailoredParams }) {
  const { lang } = useI18n();
  const taxonomy = useFitTaxonomy();
  const parts = [
    ...(params.sector ?? []),
    ...(params.region ?? []).map(regionLabel),
    ...(params.status ? [fitLabel(lang, "status", params.status, taxonomy)] : []),
    ...(params.size ? [fitLabel(lang, "size", params.size, taxonomy)] : []),
    ...(params.need ? [fitLabel(lang, "need", params.need, taxonomy)] : []),
  ];
  return (
    <div className="flex flex-wrap gap-1.5">
      {parts.map((p) => (
        <span key={p} className="text-sm px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">
          {p}
        </span>
      ))}
    </div>
  );
}

export default function TailoredView({ renderCard }: {
  renderCard: (row: OpportunityRow, why: string[]) => ReactNode;
}) {
  const { t } = useI18n();
  const [results, setResults] = useState<TailoredResult[] | null>(null);
  const [params, setParams] = useState<TailoredParams>({});
  const [error, setError] = useState(false);
  const [shown, setShown] = useState(PAGE);

  useEffect(() => {
    getTailored()
      .then((res) => {
        setParams(res.params);
        setResults(res.results);
      })
      .catch(() => setError(true));
  }, []);

  const ranked = Object.keys(params).length > 0;

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900 mb-2 font-[family-name:var(--font-dm-sans)]">
          {t("opps.tab_tailored")}
        </h1>
        <p className="text-base text-slate-600 mb-3">{t("tailored.intro")}</p>
        {ranked ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-slate-600">{t("tailored.ranked_on")}</span>
            <ProfileSummary params={params} />
            <a href="/settings" className="text-sm text-blue-600 underline hover:text-blue-800">
              {t("tailored.edit_profile")}
            </a>
          </div>
        ) : (
          <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            {t("tailored.no_profile")}{" "}
            <a href="/settings" className="underline">{t("tailored.edit_profile")}</a>
          </p>
        )}
      </div>

      {error ? (
        <div className="text-center py-16 text-base text-slate-600">{t("tailored.load_failed")}</div>
      ) : results === null ? (
        <LoadingBar label={t("tailored.loading")} />
      ) : results.length === 0 ? (
        <div className="text-center py-16 text-base text-slate-600">{t("tailored.no_results")}</div>
      ) : (
        <>
          <p className="text-sm text-slate-600 mb-3">{t("tailored.n_opportunities", { n: results.length })}</p>
          <div className="space-y-3">
            {results.slice(0, shown).map((r) => renderCard(r.row, r.why))}
          </div>
          {shown < results.length && (
            <div className="mt-6 text-center">
              <button
                onClick={() => setShown(shown + PAGE)}
                className="px-4 py-1.5 text-sm border border-slate-200 rounded-lg hover:bg-slate-50"
              >
                {t("tailored.show_more")}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
