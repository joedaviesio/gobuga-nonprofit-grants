// The public pages' header, footer, subscribe form, language links and
// JSON-LD block. Server components: no client JavaScript, no cookies.

import { serializeJsonLd } from "@/lib/jsonld";
import { LANGUAGE_NAMES } from "@/lib/public-i18n";
import { href, type Params } from "@/lib/public-urls";
import type { Site } from "@/lib/site";

export function SiteHeader({ site }: { site: Site }) {
  const nav: [string, Parameters<Site["t"]>[0]][] = [
    ["/grants", "nav_grants"],
    ["/funders", "nav_funders"],
    ["/fit", "nav_fit"],
    ["/changes", "nav_changes"],
    ["/stats", "nav_stats"],
    ["/subscribe", "nav_subscribe"],
  ];
  return (
    <header className="border-b border-slate-200">
      <div className="max-w-4xl mx-auto px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2">
        <a href={site.link("/")} aria-label={site.t("brand_home")} className="shrink-0">
          {/* eslint-disable-next-line @next/next/no-img-element -- a local SVG; no optimisation needed */}
          <img src="/gobuga-wordmark.svg" alt="GoBuga" width={120} height={36} className="h-9 w-auto" />
        </a>
        <nav aria-label={site.t("nav_label")} className="grow">
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {nav.map(([path, key]) => (
              <li key={path}>
                <a href={site.link(path)}>{site.t(key)}</a>
              </li>
            ))}
          </ul>
        </nav>
        <a href="/workspace" className="font-semibold">{site.t("nav_workspace")}</a>
      </div>
    </header>
  );
}

/** The one-field subscribe form: a plain form post, no JavaScript. */
export function SubscribeForm({ site, id }: { site: Site; id: string }) {
  return (
    <form method="post" action="/api/subscribe" className="flex flex-wrap items-end gap-2">
      <input type="hidden" name="lang" value={site.lang} />
      <div className="field">
        <label htmlFor={id}>{site.t("email_label")}</label>
        <input id={id} name="email" type="email" required autoComplete="email" maxLength={254} />
      </div>
      <button type="submit" className="button">{site.t("subscribe_button")}</button>
    </form>
  );
}

export function SiteFooter({ site }: { site: Site }) {
  return (
    <footer className="border-t border-slate-200 mt-12">
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6 text-slate-700">
        <section aria-labelledby="footer-subscribe">
          <h2 id="footer-subscribe" className="font-semibold">{site.t("nav_subscribe")}</h2>
          <p className="text-sm mb-2">{site.t("subscribe_what")}</p>
          <SubscribeForm site={site} id="footer-email" />
        </section>
        <p className="text-sm">{site.t("footer_licence")}</p>
        <p className="text-sm">
          {site.t("footer_machines")}{" "}
          <a href="/api/v1">{site.t("footer_api")}</a>{" · "}
          <a href="/llms.txt">{site.t("footer_llms")}</a>{" · "}
          {/* The MCP endpoint takes POST only, so it is named, not linked. */}
          {site.t("footer_mcp")}: <code>{site.base}/mcp</code>
        </p>
        <p className="text-sm">
          <a href="/privacy">{site.t("footer_privacy")}</a>{" · "}
          <a href="/terms">{site.t("footer_terms")}</a>{" · "}
          GoBuga · {site.countryLabel}
        </p>
      </div>
    </footer>
  );
}

/** Links to this page in the country's other UI languages; nothing on a one-language site. */
export function LanguageLinks({ site, path, params = {} }: { site: Site; path: string; params?: Params }) {
  const others = site.langs.available.filter((lang) => lang !== site.lang);
  if (others.length === 0) return null;
  return (
    <p className="text-sm text-slate-700 mb-4">
      {site.t("also_in")}{" "}
      {others.map((lang, i) => (
        <span key={lang}>
          {i > 0 && " · "}
          <a href={href(path, params, lang === site.langs.defaultLang ? null : lang)} hrefLang={lang} lang={lang}>
            {LANGUAGE_NAMES[lang] ?? lang}
          </a>
        </span>
      ))}
    </p>
  );
}

/** An inline JSON-LD block, serialised so untrusted text cannot end the script. */
export function JsonLd({ data }: { data: unknown }) {
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: serializeJsonLd(data) }} />;
}
