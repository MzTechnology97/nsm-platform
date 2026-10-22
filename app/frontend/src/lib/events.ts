import type { TFunction } from "i18next";

/** Name of an audit event in the interface language; the server's English label when the code is not translated. */
export function eventLabel(t: TFunction, type: string | null | undefined, fallback?: string | null) {
  if (!type) return fallback ?? "—";
  return t(`eventTypes.${type}`, { defaultValue: fallback ?? type });
}

/** Where an audit event came from (interface, worker, agent, connector…). */
export function eventSource(t: TFunction, source: string | null | undefined) {
  if (!source) return "—";
  return t(`eventSources.${source}`, { defaultValue: source });
}
