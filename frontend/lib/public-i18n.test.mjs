// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { chooseLang, dictionaryKeys, t } from "./public-i18n.ts";

test("New Zealand: English only", () => {
  assert.deepEqual(chooseLang(null, "en", ["en"]), { lang: "en", defaultLang: "en", available: ["en"], carry: null });
  // A language the country does not offer falls back to the default.
  assert.equal(chooseLang("ru", "en", ["en"]).lang, "en");
  assert.equal(chooseLang("ru", "en", ["en"]).carry, null);
});

test("Moldova: Romanian by default, Russian and English on request", () => {
  const md = ["ro", "ru", "en"];
  assert.deepEqual(chooseLang(null, "ro", md), { lang: "ro", defaultLang: "ro", available: md, carry: null });
  assert.deepEqual(chooseLang("ru", "ro", md), { lang: "ru", defaultLang: "ro", available: md, carry: "ru" });
  assert.equal(chooseLang("en", "ro", md).carry, "en");
  // Asking for the default carries nothing, so links stay canonical.
  assert.equal(chooseLang("ro", "ro", md).carry, null);
  assert.equal(chooseLang("fr", "ro", md).lang, "ro");
});

test("a content language without text falls back to the first offered, then English", () => {
  assert.equal(chooseLang(null, "mi", ["en"]).lang, "en");
  assert.equal(chooseLang(null, "xx", []).lang, "en");
  assert.equal(chooseLang(null, null, null).lang, "en");
  // The default is always available even if the UI list omits it.
  assert.deepEqual(chooseLang(null, "ro", ["ru"]).available, ["ro", "ru"]);
});

test("every language has every key", () => {
  const en = dictionaryKeys("en").sort();
  assert.ok(en.length > 100);
  assert.deepEqual(dictionaryKeys("ro").sort(), en);
  assert.deepEqual(dictionaryKeys("ru").sort(), en);
});

test("placeholders are the same in every language", () => {
  const holes = (s) => (s.match(/\{\w+\}/g) ?? []).sort().join(",");
  for (const key of dictionaryKeys("en")) {
    for (const lang of ["ro", "ru"]) {
      assert.equal(holes(t(lang, key)), holes(t("en", key)), `${lang} ${key}`);
    }
  }
});

test("t fills placeholders and falls back to English", () => {
  assert.equal(t("en", "deadline_dated", { date: "30 November 2026" }), "Closes 30 November 2026");
  assert.equal(t("xx", "nav_grants"), "Grants");
  assert.equal(t("ro", "nav_grants"), "Granturi");
});
