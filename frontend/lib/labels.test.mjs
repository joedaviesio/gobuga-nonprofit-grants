// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { clip, regionLabel, tagLabel } from "./labels.ts";

test("region slugs read as words", () => {
  assert.equal(regionLabel("bay-of-plenty"), "Bay of Plenty");
  assert.equal(regionLabel("auckland"), "Auckland");
  assert.equal(regionLabel("stefan-voda"), "Stefan Voda");
  assert.equal(regionLabel("of-x"), "Of X");
});

test("tag labels come from the taxonomy when it has one", () => {
  const taxonomy = { tags: [{ slug: "sport", label: "Sport & recreation" }, { slug: "maori", label: null }] };
  assert.equal(tagLabel(taxonomy, "sport"), "Sport & recreation");
  assert.equal(tagLabel(taxonomy, "maori"), "Maori");
  assert.equal(tagLabel(null, "youth"), "Youth");
});

test("clip collapses whitespace and cuts at a word", () => {
  assert.equal(clip("  a \n b  "), "a b");
  assert.equal(clip(null), "");
  const long = "word ".repeat(100);
  const out = clip(long, 50);
  assert.ok(out.length <= 50);
  assert.ok(out.endsWith("word…"));
});
