// The public 404: a complete HTML page with a real 404 status.
//
// proxy.ts sends here every path it does not recognise and every grant,
// funder or stats month the backend does not have (`/ui-<lang>/404/<path>`).
// It is a route handler, not a page calling notFound(), because with the
// root layout inside the [ui] segment Next 16 server-renders a notFound()
// page as an empty error shell and leaves the text to client JavaScript.
// Never cached: the paths are chosen by whoever makes the request.

import { escapeHtml } from "@/lib/html";
import { loadSite } from "@/lib/site";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ ui: string }> };

const NAV = [
  ["/grants", "nav_grants"],
  ["/funders", "nav_funders"],
  ["/fit", "nav_fit"],
  ["/changes", "nav_changes"],
  ["/stats", "nav_stats"],
  ["/subscribe", "nav_subscribe"],
] as const;

export async function GET(_request: Request, { params }: Context) {
  const site = await loadSite((await params).ui);
  const e = escapeHtml;
  const link = (path: string, text: string) => `<a href="${e(site.link(path))}">${e(text)}</a>`;
  const body = [
    "<!doctype html>",
    `<html lang="${e(site.lang)}"><head><meta charset="utf-8">`,
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    '<meta name="robots" content="noindex">',
    `<title>${e(site.t("not_found_title"))} | GoBuga</title>`,
    "<style>body{font-family:system-ui,sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem;",
    "line-height:1.55;color:#0f172a;background:#fff}a{color:#1d4ed8}nav ul{display:flex;flex-wrap:wrap;",
    "gap:0 1rem;list-style:none;padding:0}:focus-visible{outline:3px solid #1d4ed8;outline-offset:2px}</style>",
    "</head><body>",
    `<header><p>${link("/", "GoBuga")}</p><nav aria-label="${e(site.t("nav_label"))}"><ul>`,
    ...NAV.map(([path, key]) => `<li>${link(path, site.t(key))}</li>`),
    `</ul></nav><p><a href="/workspace">${e(site.t("nav_workspace"))}</a></p></header>`,
    `<main id="main"><h1>${e(site.t("not_found_title"))}</h1>`,
    `<p>${e(site.t("not_found_body"))}</p>`,
    `<ul><li>${link("/grants", site.t("see_all"))}</li><li>${link("/funders", site.t("nav_funders"))}</li></ul>`,
    "</main></body></html>",
    "",
  ].join("\n");
  return new Response(body, {
    status: 404,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex",
    },
  });
}
