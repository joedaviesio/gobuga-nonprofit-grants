// Request interception (Next 16's proxy.ts, formerly middleware.ts).
//
// For every request it decides, through lib/public-routes.ts:
// - workspace, API, JSON twins, feeds, click-outs and files pass untouched;
// - a public page URL asked for with `Accept: application/json` is rewritten
//   to its JSON twin on the backend (doctrine 4);
// - any other public page is rewritten under its language segment
//   (`/grants/X?lang=ru` -> `/ui-ru/grants/X`), and the hit is reported to
//   the backend, which cannot see page requests itself; a grant or funder
//   the backend does not have is sent to the public 404 first;
// - anything else is rewritten to the public 404.
//
// The JSON answer to a page URL carries `Vary: Accept`, so a cache that
// stores it keys it by Accept. The HTML answer cannot: Next 16 sets its own
// Vary header on every App Router page after the proxy and next.config
// headers have run (next/dist/build/templates/app-page.js), replacing ours.
// Next's own page cache is not affected, since JSON requests never reach it.

import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";
import { prefersJson } from "@/lib/accept";
import { API_URL, exists } from "@/lib/backend";
import { routeFor } from "@/lib/public-routes";

const HIT_TIMEOUT_MS = 3000;

/**
 * POST the page hit to the backend without delaying the page: the request
 * is handed to waitUntil and never awaited here, and every failure is
 * swallowed. Sends the path with its query string, the user agent and the
 * referrer. Never an IP address, never a cookie.
 */
function reportHit(request: NextRequest, event: NextFetchEvent): void {
  try {
    const secret = process.env.INTERNAL_HIT_SECRET;
    if (!secret || request.method !== "GET") return;
    // Client-side router fetches of a page already counted, if any ever occur.
    if (request.headers.has("rsc") || request.headers.has("next-router-prefetch")) return;
    const { pathname, search } = request.nextUrl;
    const body = JSON.stringify({
      path: (pathname + search).slice(0, 2048),
      user_agent: (request.headers.get("user-agent") ?? "").slice(0, 1024),
      referrer: request.headers.get("referer")?.slice(0, 2048) ?? null,
    });
    const sent = fetch(`${API_URL}/api/internal/hit`, {
      method: "POST",
      headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout(HIT_TIMEOUT_MS),
    }).then(
      () => undefined,
      () => undefined,
    );
    event.waitUntil(sent);
  } catch {
    // Counting must never break a page.
  }
}

export async function proxy(request: NextRequest, event: NextFetchEvent) {
  const { pathname, searchParams, search } = request.nextUrl;
  const route = routeFor(pathname, searchParams);
  if (route.kind === "pass") return NextResponse.next();

  if (route.kind === "page" && route.twin && prefersJson(request.headers.get("accept"))) {
    // The backend counts this request itself, as an API call.
    const twin = NextResponse.rewrite(new URL(`${API_URL}${route.twin}${search}`));
    twin.headers.set("Vary", "Accept");
    return twin;
  }

  const url = request.nextUrl.clone();
  url.pathname = route.internal;
  // A made-up grant ID or funder slug gets the uncached 404, not a new
  // entry in the page cache (see Lookup in lib/public-routes.ts).
  if (route.kind === "page" && route.lookup && !(await exists(route.lookup.kind, route.lookup.key))) {
    url.pathname = route.lookup.missing;
  }
  const page = NextResponse.rewrite(url);
  if (route.kind === "page") reportHit(request, event);
  return page;
}

export const config = {
  // Everything except Next's own build assets; lib/public-routes.ts decides
  // the rest, so no path can reach a public route without being classified.
  matcher: ["/((?!_next/static|_next/image).*)"],
};
