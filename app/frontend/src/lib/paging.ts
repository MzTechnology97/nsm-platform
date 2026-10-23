import { useState } from "react";

/** Rows per page offered on every list (operator request 2026-10-23); the choice is remembered in the browser. */
export const PAGE_SIZES = [50, 100, 200] as const;
const STORAGE_KEY = "nsm.pageSize";

function storedSize(): number {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    return (PAGE_SIZES as readonly number[]).includes(value) ? value : PAGE_SIZES[0];
  } catch {
    return PAGE_SIZES[0];
  }
}

export interface Paging {
  page: number;
  size: number;
  offset: number;
  setPage: (page: number) => void;
  setSize: (size: number) => void;
}

/** Current page and page size of a list; back to page 1 whenever ``resetKey`` (the filters) changes. */
export function usePaging(resetKey: string): Paging {
  const [size, setSizeState] = useState(storedSize);
  const [state, setState] = useState({ key: resetKey, page: 1 });
  const page = state.key === resetKey ? state.page : 1;
  return {
    page,
    size,
    offset: (page - 1) * size,
    setPage: (next) => setState({ key: resetKey, page: Math.max(1, next) }),
    setSize: (next) => {
      setSizeState(next);
      setState({ key: resetKey, page: 1 });
      try {
        window.localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // private window or blocked storage: the choice lasts for this page only
      }
    },
  };
}

/** ``offset`` and ``limit`` query parameters of a page. */
export function pageParams(paging: Pick<Paging, "offset" | "size">): { offset: number; limit: number } {
  return { offset: paging.offset, limit: paging.size };
}

/** ``url`` with the page's ``offset`` and ``limit`` added to its query string. */
export function withPage(url: string, page: { offset: number; limit: number }): string {
  return `${url}${url.includes("?") ? "&" : "?"}offset=${page.offset}&limit=${page.limit}`;
}
