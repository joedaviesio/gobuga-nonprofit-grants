// The fit form and the grant index filters: plain <form method="get">, so
// they work without JavaScript and every submission is a constructible URL.
// Every option comes from GET /api/v1/taxonomy.

import { formatMoney } from "@/lib/format";
import { regionLabel, tagLabel } from "@/lib/labels";
import type { PublicKey } from "@/lib/public-i18n";
import type { ApiError, FunderSummary, Taxonomy } from "@/lib/public-types";
import type { Site } from "@/lib/site";

type Option = [value: string, label: string];

function Select({ id, name, label, options, value, anyLabel }: {
  id: string;
  name: string;
  label: string;
  options: Option[];
  value: string | undefined;
  anyLabel: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} name={name} defaultValue={value ?? ""}>
        <option value="">{anyLabel}</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

function LangInput({ site }: { site: Site }) {
  return site.langs.carry ? <input type="hidden" name="lang" value={site.langs.carry} /> : null;
}

function labelled(site: Site, prefix: string, slug: string): string {
  const key = `${prefix}.${slug}` as PublicKey;
  const text = site.t(key);
  return text === key ? slug : text;
}

/** "Under $50,000", "$50,000 to $250,000", "$1,000,000 and over". */
function bandLabel(site: Site, min: number | null, max: number | null, currency: string, kind: "size" | "band"): string {
  const fmt = (v: number) => formatMoney(v, currency, site.locale);
  if (!min && max) return site.t(`${kind}_under` as PublicKey, { max: fmt(max) });
  if (min && !max) return site.t(`${kind}_over` as PublicKey, { min: fmt(min) });
  if (min && max) return site.t(`${kind}_range` as PublicKey, { min: fmt(min), max: fmt(max) });
  return "";
}

export function tagOptions(taxonomy: Taxonomy): Option[] {
  return taxonomy.tags.map((tag) => [tag.slug, tagLabel(taxonomy, tag.slug)]);
}

export function regionOptions(taxonomy: Taxonomy): Option[] {
  return taxonomy.regions.map((slug) => [slug, regionLabel(slug)]);
}

export function amountOptions(site: Site, taxonomy: Taxonomy): Option[] {
  return taxonomy.amount_bands.map((b) => [b.slug, bandLabel(site, b.min, b.max, b.currency, "band")]);
}

export function deadlineOptions(site: Site, taxonomy: Taxonomy): Option[] {
  return taxonomy.deadline_buckets.map((b) => [b.slug, labelled(site, "opt_deadline", b.slug)]);
}

export function FitForm({ site, taxonomy, values, idPrefix = "fit" }: {
  site: Site;
  taxonomy: Taxonomy;
  values: Record<string, string>;
  idPrefix?: string;
}) {
  const any = site.t("filter_any");
  const sizes: Option[] = taxonomy.size_bands.map((b) => [
    b.slug, bandLabel(site, b.revenue_min, b.revenue_max, b.currency, "size"),
  ]);
  return (
    <form method="get" action="/fit" className="flex flex-wrap items-end gap-4">
      <Select id={`${idPrefix}-sector`} name="sector" label={site.t("fit_sector")} options={tagOptions(taxonomy)}
        value={values.sector} anyLabel={any} />
      <Select id={`${idPrefix}-region`} name="region" label={site.t("fit_region")} options={regionOptions(taxonomy)}
        value={values.region} anyLabel={any} />
      <Select id={`${idPrefix}-status`} name="status" label={site.t("fit_status")}
        options={taxonomy.entity_vocab.map((slug) => [slug, labelled(site, "entity", slug)])}
        value={values.status} anyLabel={any} />
      <Select id={`${idPrefix}-size`} name="size" label={site.t("fit_size")} options={sizes}
        value={values.size} anyLabel={any} />
      <Select id={`${idPrefix}-need`} name="need" label={site.t("fit_need")}
        options={taxonomy.needs.map((slug) => [slug, labelled(site, "need", slug)])}
        value={values.need} anyLabel={any} />
      <LangInput site={site} />
      <button type="submit" className="button">{site.t("fit_submit")}</button>
    </form>
  );
}

export function GrantFilters({ site, taxonomy, funders, values }: {
  site: Site;
  taxonomy: Taxonomy;
  funders: FunderSummary[];
  values: Record<string, string>;
}) {
  const any = site.t("filter_any");
  return (
    <form method="get" action="/grants" className="flex flex-wrap items-end gap-4">
      <div className="field">
        <label htmlFor="filter-q">{site.t("filter_q")}</label>
        <input id="filter-q" name="q" type="search" defaultValue={values.q ?? ""} maxLength={200} />
      </div>
      <Select id="filter-tag" name="tag" label={site.t("filter_tag")} options={tagOptions(taxonomy)}
        value={values.tag} anyLabel={any} />
      <Select id="filter-region" name="region" label={site.t("filter_region")} options={regionOptions(taxonomy)}
        value={values.region} anyLabel={any} />
      <Select id="filter-amount" name="amount" label={site.t("filter_amount")} options={amountOptions(site, taxonomy)}
        value={values.amount} anyLabel={any} />
      <Select id="filter-deadline" name="deadline" label={site.t("filter_deadline")}
        options={deadlineOptions(site, taxonomy)} value={values.deadline} anyLabel={any} />
      <Select id="filter-funder" name="funder" label={site.t("filter_funder")}
        options={funders.map((f) => [f.slug, f.name])} value={values.funder} anyLabel={any} />
      {/* The empty option is the API's default (open grants, closing soonest
          first), so an unchanged form adds nothing to the URL. */}
      <Select id="filter-status" name="status" label={site.t("filter_status")}
        options={taxonomy.list_statuses.filter((s) => s !== "live").map((s) => [s, labelled(site, "opt_status", s)])}
        value={values.status === "live" ? "" : values.status} anyLabel={site.t("opt_status.live")} />
      <Select id="filter-sort" name="sort" label={site.t("filter_sort")}
        options={taxonomy.sorts.filter((s) => s.slug !== "deadline").map((s) => [s.slug, labelled(site, "opt_sort", s.slug)])}
        value={values.sort === "deadline" ? "" : values.sort} anyLabel={site.t("opt_sort.deadline")} />
      <LangInput site={site} />
      <button type="submit" className="button">{site.t("filter_submit")}</button>
    </form>
  );
}

const PARAM_LABELS: Record<string, PublicKey> = {
  sector: "fit_sector", size: "fit_size", need: "fit_need",
  q: "filter_q", tag: "filter_tag", region: "filter_region", amount: "filter_amount",
  deadline: "filter_deadline", funder: "filter_funder", status: "filter_status", sort: "filter_sort",
  offset: "pagination_label", limit: "pagination_label",
};

/**
 * A plain message naming the field a 400 envelope complains about.
 * `fit` picks the fit form's labels for the parameters both forms share.
 */
export function invalidMessage(site: Site, error: ApiError | null, fit = false): string {
  const match = error?.error?.message?.match(/'([a-z_]+)'/);
  const param = match?.[1];
  if (!param) return site.t("invalid_generic");
  const key = fit && param === "region" ? "fit_region" : fit && param === "status" ? "fit_status" : PARAM_LABELS[param];
  return site.t("invalid_filter", { field: key ? site.t(key) : param });
}
