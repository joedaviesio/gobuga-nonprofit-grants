// Which request paths are public pages, and where the request interception
// file (proxy.ts) sends each one.
//
// Public pages live under app/(public)/[ui]/, where `ui` is `ui-auto` (the
// country's default language) or `ui-en`, `ui-ro`, `ui-ru` (from `?lang=`).
// The language is a path segment rather than a query parameter so that each
// page can be cached per language and the root layout can set <html lang>.
// Visitors never see the segment: proxy.ts rewrites `/grants/X?lang=ru` to
// `/ui-ru/grants/X`, and sends every path it does not recognise to
// `/ui-auto/404/<path>`, the public 404 route. So a request that names an
// internal path directly (`/ui-en/grants`, `/grants-search`, `/grant-fit/X`)
// gets the public 404, not a duplicate page.
//
// Pure and dependency-free so it can be tested with `node --test`.

export const UI_LANGS = ["en", "ro", "ru"] as const;

/** The parameters of the grant index (see /api/v1/taxonomy). */
export const INDEX_PARAMS = [
  "q", "tag", "region", "amount", "deadline", "funder", "status", "sort", "offset", "limit",
] as const;

/** The five fit parameters, in the API's canonical order. */
export const FIT_PARAMS = ["sector", "region", "status", "size", "need"] as const;

// Mirrors ID_RE in api/public_data.py.
const GRANT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,79}$/;

// Paths served by the workspace layout; the proxy leaves them alone.
const WORKSPACE_PREFIXES = [
  "/workspace", "/login", "/register", "/seed", "/settings", "/case",
  "/forgot-password", "/reset-password", "/privacy", "/terms", "/setup",
];

// Served by the backend through next.config.ts rewrites, or by Next itself.
const MACHINE_PREFIXES = ["/api", "/out", "/mcp", "/_next"];

/**
 * A grant, funder or stats-month page whose key the visitor chooses. proxy.ts
 * asks the backend whether it exists before letting the request reach the
 * cached page, because Next caches a page's 404 too: without the check,
 * requests for made-up IDs would each write a new cache entry to disk.
 */
export interface Lookup {
  kind: "grant" | "funder" | "month";
  key: string;
  /** Where to send the request if it does not exist: the uncached public 404. */
  missing: string;
}

export type Route =
  // Not a public page: workspace, API, JSON twin, feed, static file.
  | { kind: "pass" }
  // A public page. `twin` is the backend route that serves its JSON.
  | { kind: "page"; internal: string; twin: string | null; lookup?: Lookup }
  // Not a known path: rendered as the public 404 page.
  | { kind: "unknown"; internal: string };

function underPrefix(pathname: string, prefixes: string[]): boolean {
  return prefixes.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

function hasAny(params: URLSearchParams, names: readonly string[]): boolean {
  return names.some((name) => (params.get(name) ?? "").trim() !== "");
}

/** `ui-auto`, or `ui-<lang>` when `?lang=` names a language we have text for. */
export function uiSegment(params: URLSearchParams): string {
  const lang = (params.get("lang") ?? "").trim().toLowerCase();
  return (UI_LANGS as readonly string[]).includes(lang) ? `ui-${lang}` : "ui-auto";
}

/** The language a `ui-*` segment asks for, or null for the country default. */
export function langFromSegment(segment: string): string | null {
  const lang = segment.startsWith("ui-") ? segment.slice(3) : "";
  return (UI_LANGS as readonly string[]).includes(lang) ? lang : null;
}

export function routeFor(pathname: string, params: URLSearchParams): Route {
  if (underPrefix(pathname, WORKSPACE_PREFIXES) || underPrefix(pathname, MACHINE_PREFIXES)) {
    return { kind: "pass" };
  }
  const segments = pathname.split("/").filter(Boolean);
  // A dot in the last segment is a file: a JSON twin, a feed, robots.txt,
  // llms.txt, sitemap.xml, an image. Grant IDs and slugs never contain one.
  if (segments.length && segments[segments.length - 1].includes(".")) {
    return { kind: "pass" };
  }
  const ui = `/${uiSegment(params)}`;
  const missing = `${ui}/404${pathname}`;
  const page = (internal: string, twin: string | null, lookup?: Lookup["kind"], key?: string): Route => ({
    kind: "page",
    internal: ui + internal,
    twin,
    ...(lookup && key ? { lookup: { kind: lookup, key, missing } } : {}),
  });
  const [first, second] = segments;

  if (segments.length === 0) return page("", null);
  if (segments.length === 1) {
    switch (first) {
      case "grants":
        // Filtered views render on request; the bare index is cached whole.
        return page(hasAny(params, INDEX_PARAMS) ? "/grants-search" : "/grants", "/api/v1/opportunities");
      case "funders":
        return page("/funders", "/api/v1/funders");
      case "fit":
        return page("/fit", "/api/v1/fit");
      case "changes":
        return page("/changes", "/api/v1/changes");
      case "stats":
        return page("/stats", "/api/v1/stats");
      case "subscribe":
        return page("/subscribe", null);
    }
  }
  if (segments.length === 2) {
    if (first === "grants" && GRANT_ID_RE.test(second)) {
      // Reached from a fit URL: the same grant, plus why it fits. Rendered on
      // request; the bare grant page stays cached.
      const internal = hasAny(params, FIT_PARAMS) ? `/grant-fit/${second}` : `/grants/${second}`;
      return page(internal, `/api/v1/opportunities/${second}`, "grant", second);
    }
    if (first === "funders") return page(`/funders/${second}`, `/api/v1/funders/${second}`, "funder", second);
    if (first === "stats") return page(`/stats/${second}`, `/api/v1/stats/${second}`, "month", second);
  }
  return { kind: "unknown", internal: missing };
}
