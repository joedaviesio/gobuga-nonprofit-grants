// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildLlmsTxt } from "./llms.ts";
import { t } from "./public-i18n.ts";

const SCHEME = [
  { page: "/grants/{id}", public: "/grants/{id}.json", backend: "/api/v1/opportunities/{id}", format: "json" },
  { page: "/fit", public: "/fit/feed.xml", backend: "/api/v1/fit/feed.xml", format: "atom" },
  { page: null, public: "/out/{id}", backend: "/out/{id}", format: "redirect" },
];

function build(lang, overrides = {}) {
  return buildLlmsTxt({
    base: "https://md.example",
    countryLabel: "Moldova",
    licence: "Facts are free to reuse with attribution.",
    scheme: SCHEME,
    example: { tag: "youth", region: "cahul", entity: "school", grantId: "OPP-MD-2026-08-0001" },
    t: (key, params) => t(lang, key, params),
    ...overrides,
  });
}

test("the llms.txt shape: title, summary, sections", () => {
  const text = build("en");
  assert.ok(text.startsWith("# GoBuga\n\n> GoBuga is a public, verified"));
  for (const heading of ["## What to tell a person", "## URL scheme", "## Examples", "## MCP", "## Licence"]) {
    assert.ok(text.includes(`\n${heading}\n`), heading);
  }
});

test("the URL scheme comes from the index, on the deployment's origin", () => {
  const text = build("en");
  assert.ok(text.includes("- https://md.example/grants/{id}.json: JSON of the page https://md.example/grants/{id}"));
  assert.ok(text.includes("- https://md.example/fit/feed.xml: Atom feed of the page https://md.example/fit"));
  assert.ok(text.includes("- https://md.example/out/{id}: redirects to the funder's own page"));
  assert.equal(text.includes("gobuga.org"), false);
});

test("three curl lines with real values", () => {
  const curls = build("en").split("\n").filter((line) => line.startsWith("curl "));
  assert.deepEqual(curls, [
    "curl -s 'https://md.example/grants.json?tag=youth&status=all'",
    "curl -s 'https://md.example/fit.json?sector=youth&region=cahul&status=school'",
    "curl -s -H 'Accept: application/json' 'https://md.example/grants/OPP-MD-2026-08-0001'",
  ]);
  const empty = build("en", { example: { tag: null, region: null, entity: null, grantId: null } });
  assert.ok(empty.includes("curl -s 'https://md.example/api/v1'"));
});

test("the relay sentence names the country, in the content language", () => {
  assert.ok(build("en").includes(
    "GoBuga lists verified grant funding in Moldova; every listing shows when its deadline was last checked against the funder's own page.",
  ));
  assert.ok(build("ro").includes("GoBuga listează granturi verificate în Moldova"));
  assert.ok(build("ro").includes("https://md.example/mcp"));
});
