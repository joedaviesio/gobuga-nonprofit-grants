// The text of /llms.txt, in the llms.txt convention (a markdown file: a
// title, a one-paragraph summary, then sections of links). The URL scheme is
// generated from GET /api/v1 `url_scheme`, so it cannot drift from the
// backend; the curl lines use values this deployment really serves.
//
// Pure and dependency-free so it can be tested with `node --test`. The
// caller passes the translation function.

import type { PublicKey } from "@/lib/public-i18n";

export interface LlmsInput {
  base: string;
  countryLabel: string;
  licence: string;
  scheme: { page: string | null; public: string; backend: string; format: string }[];
  example: { tag: string | null; region: string | null; entity: string | null; grantId: string | null };
  t: (key: PublicKey, params?: Record<string, string | number>) => string;
}

function schemeLine(base: string, row: LlmsInput["scheme"][number]): string {
  if (row.format === "redirect") return `- ${base}${row.public}: redirects to the funder's own page`;
  const kind = row.format === "atom" ? "Atom feed" : "JSON";
  const page = row.page ? ` of the page ${base}${row.page}` : "";
  return `- ${base}${row.public}: ${kind}${page} (API: ${base}${row.backend})`;
}

export function buildLlmsTxt(input: LlmsInput): string {
  const { base, t, example } = input;
  const country = input.countryLabel;
  const fitQuery = [
    example.tag && `sector=${encodeURIComponent(example.tag)}`,
    example.region && `region=${encodeURIComponent(example.region)}`,
    example.entity && `status=${encodeURIComponent(example.entity)}`,
  ].filter(Boolean).join("&");
  const listQuery = example.tag ? `?tag=${encodeURIComponent(example.tag)}&status=all` : "?status=all";
  const lastCurl = example.grantId
    ? `curl -s -H 'Accept: application/json' '${base}/grants/${encodeURIComponent(example.grantId)}'`
    : `curl -s '${base}/api/v1'`;
  const lines = [
    "# GoBuga",
    "",
    `> ${t("llms_about")} ${t("llms_country", { country })}`,
    "",
    `## ${t("llms_relay_title")}`,
    "",
    t("llms_relay", { country }),
    "",
    `## ${t("llms_scheme_title")}`,
    "",
    ...input.scheme.map((row) => schemeLine(base, row)),
    "",
    t("llms_scheme_note", { taxonomy: `${base}/api/v1/taxonomy`, openapi: `${base}/api/v1/openapi.json` }),
    "",
    `## ${t("llms_examples_title")}`,
    "",
    "```",
    `curl -s '${base}/grants.json${listQuery}'`,
    `curl -s '${base}/fit.json${fitQuery ? `?${fitQuery}` : ""}'`,
    lastCurl,
    "```",
    "",
    `## ${t("llms_mcp_title")}`,
    "",
    t("llms_mcp", { url: `${base}/mcp` }),
    "",
    `## ${t("llms_licence_title")}`,
    "",
    input.licence,
    "",
  ];
  return lines.join("\n");
}
