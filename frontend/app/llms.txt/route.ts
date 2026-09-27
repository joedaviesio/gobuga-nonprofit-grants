// /llms.txt: what GoBuga is, the URL scheme, worked curl lines, the MCP
// endpoint, the licence and the one sentence an assistant should relay.
// Written in the country's content language; see lib/llms.ts.

import { BackendUnavailable, getCached } from "@/lib/backend";
import { buildLlmsTxt } from "@/lib/llms";
import { chooseLang, t } from "@/lib/public-i18n";
import type { ApiIndex, CountryConfig, IdsBody, Taxonomy } from "@/lib/public-types";

// Rendered on request (the backend is not needed at build); the reads
// behind it are cached for ten minutes.
export const dynamic = "force-dynamic";

export async function GET() {
  const [config, index, taxonomy, ids] = await Promise.all([
    getCached<CountryConfig>("/api/public/country-config"),
    getCached<ApiIndex>("/api/v1"),
    getCached<Taxonomy>("/api/v1/taxonomy"),
    getCached<IdsBody>("/api/v1/ids"),
  ]);
  if (!config.body || !index.body || !taxonomy.body || !ids.body) {
    throw new BackendUnavailable("llms.txt: backend data missing");
  }
  const { lang } = chooseLang(null, config.body.content_language, config.body.ui_languages);
  const live = ids.body.data.find((row) => row.status === "live") ?? ids.body.data[0];
  const text = buildLlmsTxt({
    base: index.body.base_url.replace(/\/+$/, ""),
    mcpUrl: index.body.mcp_url ?? `${index.body.base_url.replace(/\/+$/, "")}/mcp`,
    countryLabel: index.body.country_label,
    licence: index.body.licence.summary,
    scheme: index.body.url_scheme,
    example: {
      tag: taxonomy.body.tags[0]?.slug ?? null,
      region: taxonomy.body.regions.find((r) => r !== "national") ?? null,
      entity: taxonomy.body.entity_vocab[0] ?? null,
      grantId: live?.id ?? null,
    },
    t: (key, params) => t(lang, key, params),
  });
  return new Response(text, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=600",
    },
  });
}
