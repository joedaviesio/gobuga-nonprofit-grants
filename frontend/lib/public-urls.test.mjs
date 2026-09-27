// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { facetHref, grantHref, href, paginate, pick, queryString } from "./public-urls.ts";

test("href keeps key order, drops empty values and puts lang last", () => {
  assert.equal(href("/grants"), "/grants");
  assert.equal(href("/grants", { tag: "sport", region: "", q: null, sort: undefined }), "/grants?tag=sport");
  assert.equal(href("/grants", { tag: "sport", region: "otago" }, "ru"), "/grants?tag=sport&region=otago&lang=ru");
  assert.equal(href("/grants", { lang: "ro", tag: "arts" }, null), "/grants?tag=arts");
  assert.equal(href("/grants", { offset: 0 }), "/grants?offset=0");
});

test("href encodes values", () => {
  assert.equal(href("/grants", { q: "youth & sport" }), "/grants?q=youth+%26+sport");
  assert.equal(href("/grants", { q: '"><script>' }), "/grants?q=%22%3E%3Cscript%3E");
});

test("pick takes the first non-empty value of each name, in the order named", () => {
  const search = { region: "otago", sector: ["sport", "arts"], status: "  ", size: undefined, other: "x" };
  assert.deepEqual(pick(search, ["sector", "region", "status", "size", "need"]), { sector: "sport", region: "otago" });
});

test("queryString is the backend query, without lang", () => {
  assert.equal(queryString({ sector: "sport", region: "otago" }), "sector=sport&region=otago");
  assert.equal(queryString({}), "");
});

test("facet and fit links are plain constructible URLs", () => {
  assert.equal(facetHref("tag", "sport", null), "/grants?tag=sport");
  assert.equal(facetHref("deadline", "30d", "ro"), "/grants?deadline=30d&lang=ro");
  assert.equal(grantHref("OPP-NZ-2026-08-0001", { sector: "sport", region: "otago" }, null),
    "/grants/OPP-NZ-2026-08-0001?sector=sport&region=otago");
  assert.equal(grantHref("OPP-NZ-2026-08-0001", {}, "en"), "/grants/OPP-NZ-2026-08-0001?lang=en");
});

test("pagination", () => {
  assert.deepEqual(paginate(45, 0, 20, 20), { from: 1, to: 20, prevOffset: null, nextOffset: 20 });
  assert.deepEqual(paginate(45, 20, 20, 20), { from: 21, to: 40, prevOffset: 0, nextOffset: 40 });
  assert.deepEqual(paginate(45, 40, 20, 5), { from: 41, to: 45, prevOffset: 20, nextOffset: null });
  assert.deepEqual(paginate(0, 0, 20, 0), { from: 0, to: 0, prevOffset: null, nextOffset: null });
  // An offset past the end still links back.
  assert.deepEqual(paginate(5, 100, 20, 0), { from: 0, to: 100, prevOffset: 80, nextOffset: null });
});
