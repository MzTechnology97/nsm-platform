import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import es from "./locales/es.json";
import fr from "./locales/fr.json";
import it from "./locales/it.json";

export const LANGUAGES = ["it", "en", "fr", "es"] as const;
export type Language = (typeof LANGUAGES)[number];
export const LANGUAGE_NAMES: Record<Language, string> = { it: "Italiano", en: "English", fr: "Français", es: "Español" };
export const resources = { it: { translation: it }, en: { translation: en }, fr: { translation: fr }, es: { translation: es } };

/** User preference, else the browser language, else Italian. */
export function pickLanguage(preferred: string | null | undefined, browser: readonly string[] = navigator.languages ?? []): Language {
  const candidates = [preferred, ...browser].filter(Boolean).map((value) => String(value).slice(0, 2).toLowerCase());
  return (candidates.find((value) => (LANGUAGES as readonly string[]).includes(value)) as Language) ?? "it";
}

void i18n.use(initReactI18next).init({
  resources,
  lng: pickLanguage(null),
  fallbackLng: "it",
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;
