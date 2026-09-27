// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  amountText, formatCalendarDate, formatInstantDate, formatMoney, formatMonth, isoDateIn, localeFor,
  previousMonths,
} from "./format.ts";

test("locale combines the UI language and the country", () => {
  assert.equal(localeFor("en", "nz"), "en-NZ");
  assert.equal(localeFor("ro", "md"), "ro-MD");
  assert.equal(localeFor("ru", "md"), "ru-MD");
});

test("a calendar date is the same day whatever the timezone", () => {
  assert.equal(formatCalendarDate("2026-11-30", "en-NZ"), "30 November 2026");
  assert.equal(formatCalendarDate("2026-01-01", "en-NZ"), "1 January 2026");
  assert.match(formatCalendarDate("2026-11-30", "ro-MD"), /30 noiembrie 2026/);
  assert.match(formatCalendarDate("2026-11-30", "ru-MD"), /30 ноября 2026/);
});

test("a malformed calendar date is null, not a wrong date", () => {
  assert.equal(formatCalendarDate("rolling", "en-NZ"), null);
  assert.equal(formatCalendarDate("2026-02-30", "en-NZ"), null);
  assert.equal(formatCalendarDate("", "en-NZ"), null);
  assert.equal(formatCalendarDate(null, "en-NZ"), null);
});

test("a timestamp is the local date in the country's timezone", () => {
  // 22:00 UTC on 3 October is 11:00 on 4 October in Auckland (NZDT).
  assert.equal(formatInstantDate("2026-10-03T22:00:00+00:00", "en-NZ", "Pacific/Auckland"), "4 October 2026");
  assert.equal(isoDateIn("2026-10-03T22:00:00+00:00", "Pacific/Auckland"), "2026-10-04");
  // The same instant is 01:00 on 4 October in Chisinau (EEST, UTC+3).
  assert.equal(isoDateIn("2026-10-03T22:00:00+00:00", "Europe/Chisinau"), "2026-10-04");
  assert.equal(isoDateIn("2026-10-03T20:00:00+00:00", "Europe/Chisinau"), "2026-10-03");
  assert.equal(formatInstantDate("not a date", "en-NZ", "Pacific/Auckland"), null);
});

test("an unknown timezone falls back to UTC instead of throwing", () => {
  assert.equal(isoDateIn("2026-10-03T22:00:00+00:00", "Not/AZone"), "2026-10-03");
});

test("months", () => {
  assert.equal(formatMonth("2026-09", "en-NZ"), "September 2026");
  assert.equal(formatMonth("2026-13", "en-NZ"), null);
  assert.deepEqual(previousMonths("2026-02", 3), ["2026-01", "2025-12", "2025-11"]);
  assert.equal(previousMonths("2026-09").length, 12);
  assert.deepEqual(previousMonths("nonsense"), []);
});

test("money in the country's currency and locale", () => {
  assert.equal(formatMoney(20000, "NZD", "en-NZ"), "$20,000");
  assert.equal(formatMoney(1500.5, "NZD", "en-NZ"), "$1,500.50");
  assert.match(formatMoney(20000, "MDL", "ro-MD"), /20\.000/);
  assert.match(formatMoney(20000, "MDL", "ro-MD"), /MDL|L/);
  // Not an ISO 4217 code: the number, then the code, never an exception.
  assert.equal(formatMoney(20000, "XX1", "en-NZ"), "20,000 XX1");
  assert.equal(formatMoney(20000, null, "en-NZ"), "20,000");
});

test("amount text says what the record states and nothing more", () => {
  const nz = (min, max) => amountText({ min, max, currency: "NZD" }, "en-NZ");
  assert.deepEqual(nz(1000, 20000), { kind: "range", min: "$1,000", max: "$20,000" });
  assert.deepEqual(nz(null, 20000), { kind: "up_to", max: "$20,000" });
  assert.deepEqual(nz(0, 20000), { kind: "up_to", max: "$20,000" });
  assert.deepEqual(nz(5000, null), { kind: "from", min: "$5,000" });
  assert.deepEqual(nz(5000, 5000), { kind: "exact", min: "$5,000" });
  assert.deepEqual(nz(null, null), { kind: "none" });
  assert.deepEqual(nz(9000, 100), { kind: "none" });
  assert.deepEqual(amountText(null, "en-NZ"), { kind: "none" });
});
