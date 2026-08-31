"use client";

import { useI18n, LANGUAGE_NAMES } from "@/lib/i18n";

/** Header language picker. Hidden on single-language deployments. */
export function LanguageSwitcher() {
  const { lang, setLang, languages } = useI18n();
  if (languages.length < 2) return null;
  return (
    <select
      value={lang}
      onChange={(e) => setLang(e.target.value)}
      aria-label="Language"
      className="text-sm border border-stone-300 rounded-md px-2 py-1 bg-white text-stone-700 cursor-pointer"
    >
      {languages.map((code) => (
        <option key={code} value={code}>
          {LANGUAGE_NAMES[code] ?? code}
        </option>
      ))}
    </select>
  );
}
