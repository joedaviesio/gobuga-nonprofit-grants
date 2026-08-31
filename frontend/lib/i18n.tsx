"use client";

// UI internationalisation. The deployment's country config decides which
// languages are offered (`ui_languages`, first entry = default). The user's
// choice persists in localStorage and overrides the deployment default.
//
// Usage:
//   const { t } = useI18n();
//   <button>{t("common.save")}</button>
//   t("opps.n_matching", { n: 12 })

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getDeploymentConfig, loadDeploymentConfig } from "@/lib/countries";
import { en } from "@/lib/locales/en";
import { ro } from "@/lib/locales/ro";
import { ru } from "@/lib/locales/ru";

export type MessageKey = keyof typeof en;
export type Catalog = Record<MessageKey, string>;

const CATALOGS: Record<string, Catalog> = { en, ro, ru };

export const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
  ro: "Română",
  ru: "Русский",
};

const STORAGE_KEY = "gobuga_lang";

function storedLang(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function resolveInitialLang(): string {
  const stored = storedLang();
  if (stored && CATALOGS[stored]) return stored;
  const deploymentDefault = getDeploymentConfig().uiLanguages[0];
  return CATALOGS[deploymentDefault] ? deploymentDefault : "en";
}

export function translate(
  lang: string,
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  const catalog = CATALOGS[lang] ?? en;
  let msg = catalog[key] ?? en[key] ?? key;
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      msg = msg.split(`{${name}}`).join(String(value));
    }
  }
  return msg;
}

interface I18nContextValue {
  lang: string;
  setLang: (lang: string) => void;
  languages: string[];
  t: (key: MessageKey, params?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue>({
  lang: "en",
  setLang: () => {},
  languages: ["en"],
  t: (key, params) => translate("en", key, params),
});

export function I18nProvider({ children }: { children: ReactNode }) {
  // Start from the synchronous default (NZ fallback / cached config) to keep
  // SSR and first client render consistent, then settle once the deployment
  // config resolves.
  const [lang, setLangState] = useState("en");
  const [languages, setLanguages] = useState<string[]>(["en"]);

  useEffect(() => {
    // Deliberate mount-time setState: SSR renders "en"; the stored/deployment
    // language can only be read on the client, so it must be applied after
    // hydration to avoid a server/client markup mismatch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLangState(resolveInitialLang());
    setLanguages(getDeploymentConfig().uiLanguages);
    loadDeploymentConfig().then((config) => {
      setLanguages(config.uiLanguages);
      if (!storedLang()) {
        const fallback = config.uiLanguages[0];
        if (CATALOGS[fallback]) setLangState(fallback);
      }
    });
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = (next: string) => {
    if (!CATALOGS[next]) return;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage unavailable — language still switches for this session
    }
    setLangState(next);
  };

  return (
    <I18nContext.Provider
      value={{
        lang,
        setLang,
        languages,
        t: (key, params) => translate(lang, key, params),
      }}
    >
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}
