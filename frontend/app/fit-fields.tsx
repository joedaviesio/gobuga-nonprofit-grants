"use client";

// The three fit parameters the grant list's "Best fit" order ranks on besides
// sector and region (api/org_fit.py): legal status, annual income and what
// the money is for.
// Asked on the sign-up screen and in settings. The allowed values come from
// the public taxonomy and the labels from the public pages' text, so the
// workspace and the public /fit form always offer the same choices.

import { useEffect, useState } from "react";
import { getFitTaxonomy, type FitParams, type FitTaxonomy } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { regionLabel } from "@/lib/labels";
import { t as publicT, type PublicKey } from "@/lib/public-i18n";

export interface FitValues {
  fit_status: string;
  fit_size: string;
  fit_need: string;
}

export const EMPTY_FIT: FitValues = { fit_status: "", fit_size: "", fit_need: "" };

export function useFitTaxonomy(): FitTaxonomy | null {
  const [taxonomy, setTaxonomy] = useState<FitTaxonomy | null>(null);
  useEffect(() => {
    getFitTaxonomy().then(setTaxonomy).catch(() => setTaxonomy(null));
  }, []);
  return taxonomy;
}

/** The label for one value, in the reader's language; the slug if unknown. */
export function fitLabel(
  lang: string,
  param: "status" | "size" | "need",
  slug: string,
  taxonomy: FitTaxonomy | null,
): string {
  if (param === "size") {
    const band = taxonomy?.size_bands.find((b) => b.slug === slug);
    if (!band) return slug;
    const fmt = (v: number) => formatMoney(v, taxonomy?.currency, lang);
    const { revenue_min: min, revenue_max: max } = band;
    if (!min && max) return publicT(lang, "size_under", { max: fmt(max) });
    if (min && !max) return publicT(lang, "size_over", { min: fmt(min) });
    if (min && max) return publicT(lang, "size_range", { min: fmt(min), max: fmt(max) });
    return slug;
  }
  const key = `${param === "status" ? "entity" : "need"}.${slug}` as PublicKey;
  const text = publicT(lang, key);
  return text === key ? slug : text;
}

export default function FitFields({ values, onChange, taxonomy, selectClassName }: {
  values: FitValues;
  onChange: (values: FitValues) => void;
  taxonomy: FitTaxonomy | null;
  selectClassName: string;
}) {
  const { lang, t } = useI18n();
  if (!taxonomy) return null;
  const fields: { key: keyof FitValues; param: "status" | "size" | "need"; label: PublicKey; options: string[] }[] = [
    { key: "fit_status", param: "status", label: "fit_status", options: taxonomy.entity_vocab },
    { key: "fit_size", param: "size", label: "fit_size", options: taxonomy.size_bands.map((b) => b.slug) },
    { key: "fit_need", param: "need", label: "fit_need", options: taxonomy.needs },
  ];
  return (
    <div className="space-y-3">
      {fields.map((f) => (
        <label key={f.key} className="block">
          <span className="block text-sm font-medium text-stone-700 mb-1">{publicT(lang, f.label)}</span>
          <select
            value={values[f.key]}
            onChange={(e) => onChange({ ...values, [f.key]: e.target.value })}
            className={selectClassName}
          >
            <option value="">{t("fit.not_set")}</option>
            {f.options.map((slug) => (
              <option key={slug} value={slug}>{fitLabel(lang, f.param, slug, taxonomy)}</option>
            ))}
          </select>
        </label>
      ))}
    </div>
  );
}

/** The profile the list is ranked on, as chips. */
export function FitSummary({ params }: { params: FitParams }) {
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
    <span className="inline-flex flex-wrap gap-1.5">
      {parts.map((p) => (
        <span key={p} className="text-sm px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">
          {p}
        </span>
      ))}
    </span>
  );
}
