// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { prefersJson } from "./accept.ts";

test("an explicit JSON request gets JSON", () => {
  assert.equal(prefersJson("application/json"), true);
  assert.equal(prefersJson("application/json; charset=utf-8"), true);
  assert.equal(prefersJson("APPLICATION/JSON"), true);
});

test("browsers get the page", () => {
  const chrome = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8";
  const firefox = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8";
  assert.equal(prefersJson(chrome), false);
  assert.equal(prefersJson(firefox), false);
});

test("curl's default, a missing header and nonsense get the page", () => {
  assert.equal(prefersJson("*/*"), false);
  assert.equal(prefersJson(""), false);
  assert.equal(prefersJson(null), false);
  assert.equal(prefersJson(undefined), false);
  assert.equal(prefersJson("garbage"), false);
  assert.equal(prefersJson(",,;;"), false);
});

test("quality values decide", () => {
  assert.equal(prefersJson("text/html;q=0.5, application/json"), true);
  assert.equal(prefersJson("application/json;q=0.5, text/html"), false);
  assert.equal(prefersJson("application/json;q=0.9, text/html;q=0.8"), true);
  assert.equal(prefersJson("application/json, */*;q=0.1"), true);
});

test("a tie goes to the page, whatever the order", () => {
  assert.equal(prefersJson("application/json, text/html"), false);
  assert.equal(prefersJson("text/html, application/json"), false);
  assert.equal(prefersJson("application/json;q=0.7, text/html;q=0.7"), false);
});

test("q=0 means not acceptable", () => {
  assert.equal(prefersJson("application/json;q=0"), false);
  assert.equal(prefersJson("application/json;q=0, text/html;q=0"), false);
});

test("wildcards count, the most specific range wins", () => {
  assert.equal(prefersJson("application/*"), true);
  assert.equal(prefersJson("application/*, text/*;q=0.5"), true);
  // text/html is rated by its own range, not by the wildcard.
  assert.equal(prefersJson("application/json;q=0.8, text/html;q=0.9, */*;q=0.1"), false);
  assert.equal(prefersJson("application/json, text/*;q=0.2"), true);
});

test("an out-of-range quality is ignored, not trusted", () => {
  // q=5 is invalid and treated as 1, so this is a tie and gets the page.
  assert.equal(prefersJson("application/json;q=5, text/html"), false);
  assert.equal(prefersJson("application/json;q=abc, text/html;q=0.5"), true);
});
