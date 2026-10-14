import i18n, { type BackendModule } from "i18next";
import { initReactI18next } from "react-i18next";

export const LANGUAGES = ["it", "en", "fr", "es"] as const;
export type Language = (typeof LANGUAGES)[number];
export const LANGUAGE_NAMES: Record<Language, string> = { it: "Italiano", en: "English", fr: "Français", es: "Español" };

/** One chunk per language: a user downloads only the language in use (and another only when switching). */
const LOADERS: Record<Language, () => Promise<{ default: Record<string, unknown> }>> = {
  it: () => import("./locales/it.json"),
  en: () => import("./locales/en.json"),
  fr: () => import("./locales/fr.json"),
  es: () => import("./locales/es.json"),
};

const lazyLocales: BackendModule = {
  type: "backend",
  init: () => undefined,
  read(language, _namespace, callback) {
    const load = LOADERS[language as Language];
    if (!load) {
      callback(null, {});
      return;
    }
    load()
      .then((module) => callback(null, module.default))
      .catch((error: Error) => callback(error, null));
  },
};

/** User preference, else the browser language, else Italian. */
export function pickLanguage(preferred: string | null | undefined, browser: readonly string[] = navigator.languages ?? []): Language {
  const candidates = [preferred, ...browser].filter(Boolean).map((value) => String(value).slice(0, 2).toLowerCase());
  return (candidates.find((value) => (LANGUAGES as readonly string[]).includes(value)) as Language) ?? "it";
}

/**
 * Resolves once the first language is loaded; the app renders after it. Every
 * language has the same keys (checked by the tests), so there is no fallback
 * language to download too. ``changeLanguage`` switches only after the new
 * bundle has arrived, so no raw keys are ever shown.
 */
export const i18nReady = i18n
  .use(lazyLocales)
  .use(initReactI18next)
  .init({
    lng: pickLanguage(null),
    fallbackLng: false,
    supportedLngs: [...LANGUAGES],
    load: "languageOnly",
    interpolation: { escapeValue: false },
    returnNull: false,
    react: { useSuspense: false },
  });

export default i18n;
