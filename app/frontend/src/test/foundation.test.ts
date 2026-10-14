import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, setCsrfToken, setNavigate } from "../api/client";
import { pickLanguage, resources } from "../i18n";

function keys(value: unknown, prefix = ""): string[] {
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => keys(child, prefix ? `${prefix}.${key}` : key));
  }
  return [prefix];
}

describe("translations", () => {
  it("every language has exactly the Italian keys", () => {
    const reference = keys(resources.it.translation).sort();
    for (const [code, bundle] of Object.entries(resources)) {
      expect(keys(bundle.translation).sort(), code).toEqual(reference);
    }
  });

  it("picks the preference, then the browser language, then Italian", () => {
    expect(pickLanguage("fr", ["en-US"])).toBe("fr");
    expect(pickLanguage(null, ["de-DE", "es-ES"])).toBe("es");
    expect(pickLanguage(null, ["de-DE"])).toBe("it");
  });
});

describe("api client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the CSRF token on changes only", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      calls.push(init);
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    setCsrfToken("token-123");
    await api("/api/ui/v1/session");
    await api("/api/ui/v1/session/preferences", { method: "PATCH", body: "{}" });
    expect(new Headers(calls[0].headers).get("X-CSRF-Token")).toBeNull();
    expect(new Headers(calls[1].headers).get("X-CSRF-Token")).toBe("token-123");
    expect(calls[1].credentials).toBe("same-origin");
  });

  it("goes to the login page when the session expired", async () => {
    const navigate = vi.fn();
    setNavigate(navigate);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    await expect(api("/api/ui/v1/session")).rejects.toBeInstanceOf(ApiError);
    expect(navigate).toHaveBeenCalledWith("/login");
  });

  it("surfaces the server message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Permesso negato." }), { status: 403 })));
    await expect(api("/x")).rejects.toThrow("Permesso negato.");
  });
});
