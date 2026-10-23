import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PingToggleDialog } from "../app/PingToggle";
import i18n from "../i18n";

vi.mock("@tanstack/react-router", () => ({ Link: ({ children }: { children: ReactNode }) => <a href="#monitor">{children}</a> }));

const settings = {
  enabled: false, target: "", effective_target: "192.0.2.10", rtt_ms: 80, loss_pct: 20, management_ip: "192.0.2.10", last_rtt: null, last_loss: null,
  last_at: null, down: false, error: null, globally_enabled: true, can_edit: true,
};

function renderDialog(onClose = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><PingToggleDialog deviceId="dev-1" onClose={onClose} /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ping on/off window", () => {
  it("switches the ping on keeping address and thresholds", async () => {
    await i18n.changeLanguage("it");
    const calls: { url: string; body?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      const payload = init?.method === "POST" ? { ...settings, enabled: true } : settings;
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    renderDialog();
    const toggle = await screen.findByRole("switch");
    expect((toggle as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText("Indirizzo interrogato: 192.0.2.10")).toBeTruthy();
    fireEvent.click(toggle);
    await waitFor(() => expect(calls.some((call) => call.body)).toBe(true));
    const sent = JSON.parse(calls.find((call) => call.body)!.body!);
    expect(sent).toEqual({ enabled: true, target: "", rtt_ms: "80", loss_pct: "20" });
    expect(await screen.findByText(/Ping attivato/)).toBeTruthy();
  });

  it("is read-only without the permission and closes with Escape", async () => {
    await i18n.changeLanguage("it");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...settings, can_edit: false }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const onClose = vi.fn();
    renderDialog(onClose);
    expect(((await screen.findByRole("switch")) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/serve il permesso/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
