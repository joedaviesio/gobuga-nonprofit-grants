// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { onboardingRedirect } from "./onboarding.ts";

const NEW = { setup_complete: false, seeding_complete: false };
const OLD_FLOW_HALFWAY = { setup_complete: true, seeding_complete: false };
const SEEDED_NOT_SET_UP = { setup_complete: false, seeding_complete: true };
const DONE = { setup_complete: true, seeding_complete: true };

test("a new account is sent to the one screen from anywhere", () => {
  assert.equal(onboardingRedirect(NEW, "/workspace"), "/seed");
  assert.equal(onboardingRedirect(NEW, "/settings"), "/seed");
  assert.equal(onboardingRedirect(NEW, "/case/abc"), "/seed");
});

test("an account part-way through the old two-step flow lands on the one screen", () => {
  assert.equal(onboardingRedirect(OLD_FLOW_HALFWAY, "/workspace"), "/seed");
  assert.equal(onboardingRedirect(OLD_FLOW_HALFWAY, "/seed"), null);
});

test("an account seeded but never set up lands on the one screen", () => {
  assert.equal(onboardingRedirect(SEEDED_NOT_SET_UP, "/workspace"), "/seed");
  assert.equal(onboardingRedirect(SEEDED_NOT_SET_UP, "/seed"), null);
});

test("the one screen does not redirect to itself", () => {
  assert.equal(onboardingRedirect(NEW, "/seed"), null);
});

test("a finished account stays where it is and is sent from the screen to the workspace", () => {
  assert.equal(onboardingRedirect(DONE, "/workspace"), null);
  assert.equal(onboardingRedirect(DONE, "/settings"), null);
  assert.equal(onboardingRedirect(DONE, "/seed"), "/workspace");
});

test("no state loops: following redirects always settles within one hop", () => {
  const states = [NEW, OLD_FLOW_HALFWAY, SEEDED_NOT_SET_UP, DONE];
  for (const flags of states) {
    for (const start of ["/workspace", "/seed", "/settings", "/case/x"]) {
      const next = onboardingRedirect(flags, start);
      if (next !== null) assert.equal(onboardingRedirect(flags, next), null, `${JSON.stringify(flags)} ${start}`);
    }
  }
});
