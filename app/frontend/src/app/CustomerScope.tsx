import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { createContext, type ReactNode, useContext } from "react";

/**
 * List pages (devices, backups, activity…) open globally or as a tab of a customer: inside a customer they keep the
 * customer's page around them and show that customer only (operator request 2026-10-23).
 */
const ScopeContext = createContext<string | null>(null);

/** The customer whose tab the page is shown in, or null on the global page. */
export function useCustomerScope(): string | null {
  return useContext(ScopeContext);
}

/** Wraps a list page shown as a tab of the customer in the URL. */
export function CustomerScoped({ children }: { children: ReactNode }) {
  const { customerId } = useParams({ strict: false }) as { customerId?: string };
  return <ScopeContext.Provider value={customerId ?? null}>{children}</ScopeContext.Provider>;
}

/**
 * Filters of a list page from its URL, wherever it is mounted. Inside a customer the customer filter is fixed:
 * ``search`` carries it, ``chosen`` holds only what the user picked (for "filters active" and "reset").
 */
export function useListSearch<T extends { customer?: string }>() {
  const customer = useCustomerScope();
  const chosen = useSearch({ strict: false } as never) as T; // whichever route the page is mounted on
  const navigate = useNavigate();
  const search = (customer ? { ...chosen, customer } : chosen) as T;
  const update = (patch: Partial<T>) =>
    void navigate({ to: ".", search: (previous: T) => ({ ...previous, ...patch }), replace: true } as never);
  const reset = () => void navigate({ to: ".", search: {}, replace: true } as never);
  return { search, chosen, update, reset, customer };
}
