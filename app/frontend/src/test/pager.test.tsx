import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Pager } from "../app/Pager";
import i18n from "../i18n";
import { usePaging, withPage } from "../lib/paging";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("list pages", () => {
  it("adds offset and limit to any list URL", () => {
    expect(withPage("/api/ui/v1/customers", { offset: 100, limit: 50 })).toBe("/api/ui/v1/customers?offset=100&limit=50");
    expect(withPage("/api/ui/v1/firmware?state=all", { offset: 0, limit: 200 })).toBe("/api/ui/v1/firmware?state=all&offset=0&limit=200");
  });

  it("starts at page 1 with 50 rows, remembers the size, and goes back to page 1 when the filters change", () => {
    const { result, rerender } = renderHook(({ key }) => usePaging(key), { initialProps: { key: "a" } });
    expect([result.current.page, result.current.size, result.current.offset]).toEqual([1, 50, 0]);
    act(() => result.current.setPage(3));
    expect(result.current.offset).toBe(100);
    act(() => result.current.setSize(200));
    expect([result.current.page, result.current.size]).toEqual([1, 200]);
    expect(window.localStorage.getItem("nsm.pageSize")).toBe("200");
    act(() => result.current.setPage(2));
    rerender({ key: "b" });
    expect(result.current.page).toBe(1);
  });

  it("shows the range and the pages, and nothing for a short list", async () => {
    await i18n.changeLanguage("it");
    const paging = { page: 2, size: 50, offset: 50, setPage: () => undefined, setSize: () => undefined };
    const pages: number[] = [];
    const { rerender } = render(<Pager total={838} paging={{ ...paging, setPage: (n) => pages.push(n) }} />);
    expect(screen.getByText("51–100 di 838")).toBeTruthy();
    expect(screen.getByRole("button", { current: "page" }).textContent).toBe("2");
    expect(screen.getByRole("button", { name: "17" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pagina successiva" }));
    expect(pages).toEqual([3]);
    rerender(<Pager total={30} paging={{ ...paging, page: 1, offset: 0 }} />);
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});
