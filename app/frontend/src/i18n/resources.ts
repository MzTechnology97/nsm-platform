// Every language at once, for the tests only (the app loads one language at a time).
import en from "./locales/en.json";
import es from "./locales/es.json";
import fr from "./locales/fr.json";
import it from "./locales/it.json";

export const resources = { it: { translation: it }, en: { translation: en }, fr: { translation: fr }, es: { translation: es } };
