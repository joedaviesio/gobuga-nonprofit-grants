// Per-render context for the public pages: the country, the page language,
// the locale and timezone for dates, the public origin, and helpers that
// build links and metadata. Server only (reads through lib/backend.ts).

import type { Metadata } from "next";
import { BackendUnavailable, getCached } from "@/lib/backend";
import { localeFor } from "@/lib/format";
import { chooseLang, t, type LangChoice, type PublicKey } from "@/lib/public-i18n";
import { langFromSegment } from "@/lib/public-routes";
import type { ApiIndex, CountryConfig, Taxonomy } from "@/lib/public-types";
import { href, type Params } from "@/lib/public-urls";

export interface Site {
  country: string;
  countryLabel: string;
  currency: string;
  timezone: string;
  locale: string;
  lang: string;
  langs: LangChoice;
  /** Public origin, from the backend's APP_URL (GET /api/v1 base_url). */
  base: string;
  index: ApiIndex;
  t: (key: PublicKey, params?: Record<string, string | number>) => string;
  /** An internal link, carrying `lang=` when the visitor chose a non-default language. */
  link: (path: string, params?: Params) => string;
}

export async function loadSite(ui: string): Promise<Site> {
  const [config, index] = await Promise.all([
    getCached<CountryConfig>("/api/public/country-config"),
    getCached<ApiIndex>("/api/v1"),
  ]);
  if (!config.body || !index.body) throw new BackendUnavailable("country config or API index missing");
  const cfg = config.body;
  const langs = chooseLang(langFromSegment(ui), cfg.content_language, cfg.ui_languages);
  return {
    country: cfg.country,
    countryLabel: cfg.country_label,
    currency: cfg.currency,
    timezone: cfg.timezone || "UTC",
    locale: localeFor(langs.lang, cfg.country),
    lang: langs.lang,
    langs,
    base: index.body.base_url.replace(/\/+$/, ""),
    index: index.body,
    t: (key, params) => t(langs.lang, key, params),
    link: (path, params) => href(path, params, langs.carry),
  };
}

export async function loadTaxonomy(): Promise<Taxonomy> {
  const res = await getCached<Taxonomy>("/api/v1/taxonomy");
  if (!res.body) throw new BackendUnavailable("taxonomy missing");
  return res.body;
}

export interface PageMeta {
  path: string;                 // the page's public path, e.g. /grants/OPP-...
  title: string | { absolute: string };
  description: string;
  json?: string | null;         // public path of the JSON twin
  atom?: string | null;         // public path of an Atom feed
  noindex?: boolean;
  canonical?: string | null;    // absolute canonical URL, when not the page's own
}

/**
 * Title, description, canonical, JSON and Atom alternates, hreflang
 * alternates (when the country offers several languages), Open Graph basics
 * and robots.
 */
export function pageMetadata(site: Site, meta: PageMeta): Metadata {
  const { langs, base } = site;
  const own = base + href(meta.path, {}, langs.carry);
  const canonical = meta.canonical ?? own;
  const languages: Record<string, string> = {};
  if (langs.available.length > 1) {
    for (const lang of langs.available) {
      languages[lang] = base + href(meta.path, {}, lang === langs.defaultLang ? null : lang);
    }
    languages["x-default"] = base + meta.path;
  }
  const types: Record<string, string> = {};
  if (meta.json) types["application/json"] = base + meta.json;
  if (meta.atom) types["application/atom+xml"] = base + meta.atom;
  return {
    title: meta.title,
    description: meta.description,
    alternates: { canonical, languages, types },
    openGraph: {
      title: typeof meta.title === "string" ? meta.title : meta.title.absolute,
      description: meta.description,
      url: canonical,
      siteName: "GoBuga",
      type: "website",
      locale: site.locale.replace("-", "_"),
    },
    robots: meta.noindex ? { index: false, follow: true } : undefined,
  };
}
