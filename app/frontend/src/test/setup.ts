import i18n, { i18nReady } from "../i18n";
import { resources } from "../i18n/resources";

// Tests read any language synchronously (getFixedT): load all bundles up front.
await i18nReady;
for (const [language, bundle] of Object.entries(resources)) i18n.addResourceBundle(language, "translation", bundle.translation, true, true);
