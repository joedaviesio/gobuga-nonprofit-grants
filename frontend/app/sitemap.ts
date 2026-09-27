// /sitemap.xml: the fixed public pages, every funder page, and every live
// and closed grant with lastModified from its last_seen. Never a stale grant
// (GET /api/v1/ids leaves them out) and never a filtered or paginated URL.
//
// One file holds at most 50,000 URLs. The pool is far smaller today; past
// that, this needs splitting with generateSitemaps. Not built for it now.

import type { MetadataRoute } from "next";
import { BackendUnavailable, getCached } from "@/lib/backend";
import type { ApiIndex, FunderSummary, IdsBody, ListBody } from "@/lib/public-types";

// Rendered on request (the backend is not needed at build); the reads
// behind it are cached for ten minutes.
export const dynamic = "force-dynamic";

const FIXED = ["/", "/grants", "/funders", "/fit", "/changes", "/stats", "/subscribe"];

async function allFunders(): Promise<FunderSummary[]> {
  const out: FunderSummary[] = [];
  for (let offset = 0; ; offset += 100) {
    const res = await getCached<ListBody<FunderSummary>>(`/api/v1/funders?offset=${offset}&limit=100`);
    if (!res.body) throw new BackendUnavailable("funders missing");
    out.push(...res.body.data);
    if (res.body.data.length === 0 || out.length >= res.body.total) return out;
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const [index, ids, funders] = await Promise.all([
    getCached<ApiIndex>("/api/v1"),
    getCached<IdsBody>("/api/v1/ids"),
    allFunders(),
  ]);
  if (!index.body || !ids.body) throw new BackendUnavailable("API index or ids missing");
  const base = index.body.base_url.replace(/\/+$/, "");
  const published = index.body.meta.published_at ?? undefined;
  return [
    ...FIXED.map((path) => ({ url: `${base}${path}`, lastModified: published })),
    ...funders.map((f) => ({ url: `${base}/funders/${f.slug}`, lastModified: published })),
    ...ids.body.data
      .filter((row) => row.status === "live" || row.status === "closed")
      .map((row) => ({ url: `${base}/grants/${row.id}`, lastModified: row.last_seen ?? undefined })),
  ];
}
