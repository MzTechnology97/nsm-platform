import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectorDiagnostics } from "../app/ConnectorDiagnostics";
import i18n from "../i18n";

const report = {
  provider: "uisp", at: "2026-10-21T15:00:00+00:00", nsm_version: "0.49.189",
  endpoints: [
    { name: "GET /devices", status: 200, rows: 3, error: null, keys: ["overview.wirelessSignal:number ×3"],
      fields: [
        { field: "mac", expected: ["identification.mac", "mac"], found: 3, rows: 3, path: "identification.mac", type: "text", state: "ok" },
        { field: "metrica signal", expected: ["overview.signal"], found: 0, rows: 3, path: null, type: null, state: "missing" },
      ] },
    { name: "GET /devices/{id}/backups", status: 404, rows: 0, error: null, keys: [], fields: [] },
  ],
  missing: ["GET /devices: metrica signal"], failed_endpoints: ["GET /devices/{id}/backups"], text: "Diagnosi connettore uisp",
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ConnectorDiagnostics provider="uisp" /></QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe("connector compatibility panel", () => {
  it("runs the check and shows endpoints, missing fields and the keys received", async () => {
    await i18n.changeLanguage("it");
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify({ report: init?.method === "POST" ? report : null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();
    expect(await screen.findByText("Nessuna diagnosi eseguita.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Esegui diagnosi" }));
    await waitFor(() => expect(screen.getByText(/1 campo atteso non arriva: GET \/devices: metrica signal/)).toBeTruthy());
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true);
    expect(screen.getAllByText("mancante").length).toBeGreaterThan(0);
    expect(screen.getByText("identification.mac (text)")).toBeTruthy();
    expect(screen.getByText("overview.signal")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copia rapporto" })).toBeTruthy();
  });
});
