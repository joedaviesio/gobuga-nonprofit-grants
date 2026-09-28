// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { langFromSegment, routeFor, uiSegment } from "./public-routes.ts";

const route = (path, query = "") => routeFor(path, new URLSearchParams(query));

test("public pages are rewritten under the language segment, with their twin", () => {
  assert.deepEqual(route("/"), { kind: "page", internal: "/ui-auto", twin: null });
  assert.deepEqual(route("/grants"), { kind: "page", internal: "/ui-auto/grants", twin: "/api/v1/opportunities" });
  assert.deepEqual(route("/grants/OPP-NZ-2026-08-0001"), {
    kind: "page", internal: "/ui-auto/grants/OPP-NZ-2026-08-0001", twin: "/api/v1/opportunities/OPP-NZ-2026-08-0001",
    lookup: { kind: "grant", key: "OPP-NZ-2026-08-0001", missing: "/ui-auto/404/grants/OPP-NZ-2026-08-0001" },
  });
  assert.deepEqual(route("/funders"), { kind: "page", internal: "/ui-auto/funders", twin: "/api/v1/funders" });
  assert.deepEqual(route("/funders/foundation-north"), {
    kind: "page", internal: "/ui-auto/funders/foundation-north", twin: "/api/v1/funders/foundation-north",
    lookup: { kind: "funder", key: "foundation-north", missing: "/ui-auto/404/funders/foundation-north" },
  });
  assert.deepEqual(route("/fit"), { kind: "page", internal: "/ui-auto/fit", twin: "/api/v1/fit" });
  assert.deepEqual(route("/changes"), { kind: "page", internal: "/ui-auto/changes", twin: "/api/v1/changes" });
  assert.deepEqual(route("/stats"), { kind: "page", internal: "/ui-auto/stats", twin: "/api/v1/stats" });
  assert.deepEqual(route("/stats/2026-09"), {
    kind: "page", internal: "/ui-auto/stats/2026-09", twin: "/api/v1/stats/2026-09",
    lookup: { kind: "month", key: "2026-09", missing: "/ui-auto/404/stats/2026-09" },
  });
  assert.deepEqual(route("/subscribe"), { kind: "page", internal: "/ui-auto/subscribe", twin: null });
});

test("?lang= picks the segment; unknown languages get the default", () => {
  assert.equal(route("/grants", "lang=ru").internal, "/ui-ru/grants");
  assert.equal(route("/", "lang=ro").internal, "/ui-ro");
  assert.equal(route("/", "lang=RU").internal, "/ui-ru");
  assert.equal(route("/", "lang=fr").internal, "/ui-auto");
  assert.equal(uiSegment(new URLSearchParams("lang=../x")), "ui-auto");
  assert.equal(langFromSegment("ui-ru"), "ru");
  assert.equal(langFromSegment("ui-auto"), null);
  assert.equal(langFromSegment("ui-xx"), null);
  assert.equal(langFromSegment("grants"), null);
});

test("a filtered index and a grant reached from a fit URL render on request", () => {
  assert.equal(route("/grants", "tag=sport").internal, "/ui-auto/grants-search");
  assert.equal(route("/grants", "offset=20").internal, "/ui-auto/grants-search");
  // Empty parameters, as a GET form submits them, do not count.
  assert.equal(route("/grants", "q=&tag=&lang=ro").internal, "/ui-ro/grants");
  assert.equal(route("/grants/OPP-1", "sector=sport").internal, "/ui-auto/grant-fit/OPP-1");
  assert.equal(route("/grants/OPP-1", "sector=&region=").internal, "/ui-auto/grants/OPP-1");
  assert.equal(route("/grants/OPP-1", "sector=sport").twin, "/api/v1/opportunities/OPP-1");
  assert.equal(route("/grants/OPP-1", "sector=sport&lang=ro").lookup.missing, "/ui-ro/404/grants/OPP-1");
});

test("workspace, API, twins, feeds, click-outs and files pass through untouched", () => {
  for (const path of [
    "/workspace", "/login", "/register", "/seed", "/settings", "/case/abc", "/forgot-password",
    "/reset-password", "/privacy", "/terms", "/setup", "/api/v1/opportunities", "/api/subscribe",
    "/out/OPP-1", "/mcp", "/_next/static/chunk.js", "/grants/OPP-1.json", "/grants.json",
    "/fit/feed.xml", "/changes/feed.xml", "/stats/2026-09.json", "/robots.txt", "/sitemap.xml",
    "/llms.txt", "/favicon.ico", "/gobuga-wordmark.svg",
  ]) {
    assert.deepEqual(route(path), { kind: "pass" }, path);
  }
});

test("anything else is the public 404, including direct requests for internal paths", () => {
  assert.deepEqual(route("/nope"), { kind: "unknown", internal: "/ui-auto/404/nope" });
  assert.deepEqual(route("/nope", "lang=ru"), { kind: "unknown", internal: "/ui-ru/404/nope" });
  assert.deepEqual(route("/ui-en/grants"), { kind: "unknown", internal: "/ui-auto/404/ui-en/grants" });
  // Internal route names are not public paths.
  assert.deepEqual(route("/grants-search"), { kind: "unknown", internal: "/ui-auto/404/grants-search" });
  assert.deepEqual(route("/grant-fit/OPP-1"), { kind: "unknown", internal: "/ui-auto/404/grant-fit/OPP-1" });
  assert.deepEqual(route("/grants/OPP-1/fit"), { kind: "unknown", internal: "/ui-auto/404/grants/OPP-1/fit" });
  assert.equal(route("/grants/bad_id!").kind, "unknown");
  assert.equal(route("/workspaces").kind, "unknown");
});
