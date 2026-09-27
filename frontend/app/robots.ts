// /robots.txt: everything public is open to every crawler, and the AI and
// search crawlers are named so that no one has to guess (being crawled is
// the point, doctrine 10). Click-outs, the internal API and the workspace
// are closed; the machine API under /api/v1/ is open.
//
// The origin comes from the backend's APP_URL (GET /api/v1 base_url), the
// same setting every canonical URL uses, so it is right on every deployment.

import type { MetadataRoute } from "next";
import { BackendUnavailable, getCached } from "@/lib/backend";
import type { ApiIndex } from "@/lib/public-types";

// Rendered on request (the backend is not needed at build); the index
// behind it is cached for ten minutes.
export const dynamic = "force-dynamic";

const NAMED_CRAWLERS = [
  "GPTBot", "OAI-SearchBot", "ChatGPT-User",
  "ClaudeBot", "Claude-SearchBot", "Claude-User",
  "PerplexityBot", "Perplexity-User",
  "Google-Extended", "CCBot",
  "Googlebot", "Bingbot",
];

const DISALLOW = [
  "/out/", "/api/",
  "/workspace", "/login", "/register", "/seed", "/setup", "/settings", "/case/",
  "/forgot-password", "/reset-password",
];

export default async function robots(): Promise<MetadataRoute.Robots> {
  const index = await getCached<ApiIndex>("/api/v1");
  if (!index.body) throw new BackendUnavailable("API index missing");
  const base = index.body.base_url.replace(/\/+$/, "");
  // A crawler obeys only the group that names it, so each group carries the
  // full rules. `Allow: /api/v1/` is longer than `Disallow: /api/`, so it wins.
  const rules = { allow: ["/", "/api/v1/"], disallow: DISALLOW };
  return {
    rules: [
      { userAgent: NAMED_CRAWLERS, ...rules },
      { userAgent: "*", ...rules },
    ],
    sitemap: `${base}/sitemap.xml`,
  };
}
