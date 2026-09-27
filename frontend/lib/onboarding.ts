// Where a signed-in user belongs while onboarding. Sign-up is register, then
// one optional screen (/seed), then the feed. The screen sets both session
// flags (POST /api/org/setup, then POST /api/org/seeding-complete), whether
// the person fills it in or skips it.
//
// Every account state resolves to one of two places, so nothing loops:
//   neither flag set          new account                    -> /seed
//   setup done, seeding not   part-way through the old flow  -> /seed
//   seeding done, setup not   only reachable through the API -> /seed
//   both set                  finished (legacy orgs report
//                             seeding_complete true)         -> stays; /seed -> /
//
// Pure and dependency-free so it can be tested with `node --test`.

export const ONBOARDING_PATH = "/seed";

export interface OnboardingFlags {
  setup_complete: boolean;
  seeding_complete: boolean;
}

export function onboardingDone(flags: OnboardingFlags): boolean {
  return flags.setup_complete && flags.seeding_complete;
}

/** The path to send the user to, or null when they may stay on `pathname`. */
export function onboardingRedirect(flags: OnboardingFlags, pathname: string): string | null {
  if (!onboardingDone(flags)) {
    return pathname === ONBOARDING_PATH ? null : ONBOARDING_PATH;
  }
  return pathname === ONBOARDING_PATH ? "/" : null;
}
