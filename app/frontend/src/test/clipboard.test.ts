import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText } from "../lib/clipboard";

describe("copyText", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses the clipboard API on HTTPS", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    expect(await copyText("a")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("a");
  });

  it("falls back to a hidden text area over plain HTTP and removes it", async () => {
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec;
    expect(await copyText("/tool fetch url=http://192.0.2.10")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.querySelector(".ui-copy-buffer")).toBeNull();
  });

  it("reports failure when the browser refuses both", async () => {
    vi.stubGlobal("isSecureContext", false);
    document.execCommand = vi.fn().mockReturnValue(false);
    expect(await copyText("x")).toBe(false);
  });
});
