// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { datasetJsonLd, grantJsonLd, serializeJsonLd } from "./jsonld.ts";

// The hostile title from scripts/dev_seed_public_fixture.py.
const HOSTILE = `Youth "Sports" Fund </script><script>alert(1)</script> & 'friends'`;

const GRANT = {
  id: "OPP-NZ-2026-08-0001",
  title: "Community Sport Fund",
  summary: "Funding for community sport clubs.",
  canonical_url: "https://example.test/grants/OPP-NZ-2026-08-0001",
  funder: { name: "Foundation North", url: "https://example.test/funders/foundation-north" },
  deadline: { state: "dated", date: "2026-11-30" },
  amount: { min: 1000, max: 20000, currency: "NZD" },
};

test("serialised JSON-LD cannot close its script element", () => {
  const out = serializeJsonLd({ name: HOSTILE });
  assert.equal(out.includes("<"), false);
  assert.equal(out.includes(">"), false);
  assert.equal(out.includes("&"), false);
  assert.equal(out.toLowerCase().includes("</script"), false);
  // Still JSON, and it reads back to the exact original string.
  assert.equal(JSON.parse(out).name, HOSTILE);
});

test("line and paragraph separators are escaped", () => {
  const out = serializeJsonLd({ s: "a\u2028b\u2029c" });
  assert.equal(out.includes("\u2028"), false);
  assert.equal(out.includes("\u2029"), false);
  assert.equal(JSON.parse(out).s, "a\u2028b\u2029c");
});

test("a grant becomes a MonetaryGrant with funder and amount", () => {
  const ld = grantJsonLd(GRANT);
  assert.equal(ld["@context"], "https://schema.org");
  assert.equal(ld["@type"], "MonetaryGrant");
  assert.equal(ld.identifier, GRANT.id);
  assert.equal(ld.url, GRANT.canonical_url);
  assert.equal(ld.name, "Community Sport Fund");
  assert.equal(ld.description, GRANT.summary);
  assert.deepEqual(ld.funder, { "@type": "Organization", name: "Foundation North", url: GRANT.funder.url });
  assert.deepEqual(ld.amount, {
    "@type": "MonetaryAmount", currency: "NZD", minValue: 1000, maxValue: 20000, validThrough: "2026-11-30",
  });
});

test("the hostile title survives building and serialising intact and inert", () => {
  const out = serializeJsonLd(grantJsonLd({ ...GRANT, title: HOSTILE }));
  assert.equal(out.toLowerCase().includes("</script"), false);
  assert.equal(JSON.parse(out).name, HOSTILE);
});

test("nothing is stated that the record leaves empty", () => {
  const ld = grantJsonLd({
    ...GRANT,
    summary: null,
    deadline: { state: "rolling-confirmed", date: null },
    amount: { min: null, max: null, currency: "NZD" },
  });
  assert.equal("description" in ld, false);
  assert.equal("amount" in ld, false);
});

test("a rolling deadline has no validThrough; an amount keeps only its stated bounds", () => {
  const ld = grantJsonLd({ ...GRANT, deadline: { state: "rolling-confirmed", date: null }, amount: { min: null, max: 5000, currency: "NZD" } });
  assert.deepEqual(ld.amount, { "@type": "MonetaryAmount", currency: "NZD", maxValue: 5000 });
});

test("a closed grant's past date is not restated as a closing date", () => {
  const ld = grantJsonLd({ ...GRANT, deadline: { state: "closed", date: null } });
  assert.equal("validThrough" in ld.amount, false);
});

test("a contradictory amount is left out, as the page leaves it out", () => {
  const ld = grantJsonLd({ ...GRANT, amount: { min: 9000, max: 100, currency: "NZD" }, deadline: { state: "closed", date: null } });
  assert.equal("amount" in ld, false);
});

test("the dataset lists its downloads", () => {
  const ld = datasetJsonLd({
    name: "GoBuga grants: New Zealand",
    description: "Every grant.",
    url: "https://example.test/grants",
    licence: "attribution-required",
    publisherName: "GoBuga",
    publisherUrl: "https://example.test",
    spatialCoverage: "New Zealand",
    dateModified: "2026-09-25T00:00:00+00:00",
    downloads: [{ url: "https://example.test/grants.json", encodingFormat: "application/json", name: "Index" }],
  });
  assert.equal(ld["@type"], "Dataset");
  assert.equal(ld.distribution[0]["@type"], "DataDownload");
  assert.equal(ld.distribution[0].contentUrl, "https://example.test/grants.json");
  assert.equal(ld.dateModified, "2026-09-25T00:00:00+00:00");
});
