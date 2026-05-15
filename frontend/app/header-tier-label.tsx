"use client";

import { useAuth } from "./auth-gate";

export function HeaderTierLabel() {
  const { session } = useAuth();

  const isOfficer = session?.tier === "officer";
  const label = isOfficer ? (session?.tier_label || "Grant Officer") : "Grant Scanner";

  return (
    <span className={`text-sm font-medium font-[family-name:var(--font-dm-sans)] ${
      isOfficer ? "text-blue-600" : "text-stone-600"
    }`}>
      {label}
    </span>
  );
}
