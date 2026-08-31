"use client";

import { useAuth } from "./auth-gate";
import { useI18n } from "@/lib/i18n";

export function HeaderTierLabel() {
  const { session } = useAuth();
  const { t } = useI18n();

  const isOfficer = session?.tier === "officer";
  const label = isOfficer
    ? (session?.tier_label || t("tiers.officer"))
    : t("tiers.scanner");

  return (
    <span className={`text-sm font-medium font-[family-name:var(--font-dm-sans)] ${
      isOfficer ? "text-blue-600" : "text-stone-600"
    }`}>
      {label}
    </span>
  );
}
