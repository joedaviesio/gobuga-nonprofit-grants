// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { LruCache } from "./lru.ts";

test("holds at most maxEntries, evicting the least recently used", () => {
  const cache = new LruCache(2, 1000, () => 0);
  cache.set("a", 1);
  cache.set("b", 2);
  assert.equal(cache.get("a"), 1); // a is now the most recently used
  cache.set("c", 3);
  assert.equal(cache.size, 2);
  assert.equal(cache.get("b"), undefined);
  assert.equal(cache.get("a"), 1);
  assert.equal(cache.get("c"), 3);
});

test("entries expire after the time to live", () => {
  let now = 0;
  const cache = new LruCache(10, 600_000, () => now);
  cache.set("k", "v");
  now = 599_999;
  assert.equal(cache.get("k"), "v");
  now = 600_000;
  assert.equal(cache.get("k"), undefined);
  assert.equal(cache.size, 0);
});

test("a flood of distinct keys never grows it past its bound", () => {
  const cache = new LruCache(50, 1000, () => 0);
  for (let i = 0; i < 10_000; i++) cache.set(`key-${i}`, i);
  assert.equal(cache.size, 50);
  assert.equal(cache.get("key-9999"), 9999);
  assert.equal(cache.get("key-0"), undefined);
});
