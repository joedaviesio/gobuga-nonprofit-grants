"use client";

import { useAuth } from "./auth-gate";

export function HeaderTierLabel() {
  const { session } = useAuth();

  const label = session?.tier_label || "Grant Scanner";
  const isOfficer = session?.tier === "officer";

  return (
    <span className={`text-xs font-[family-name:var(--font-dm-sans)] ${
      isOfficer ? "text-blue-500" : "text-stone-400"
    }`}>
      {label}
    </span>
  );
}
