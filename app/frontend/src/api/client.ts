/**
 * Fetch wrapper for the NSM UI API (same origin, session cookie).
 *
 * - every change sends the session CSRF token in X-CSRF-Token;
 * - 401 means the session expired: the browser goes to the classic login page;
 * - errors carry the server message (FastAPI "detail").
 */

let csrfToken = "";

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type Navigate = (url: string) => void;
let navigate: Navigate = (url) => window.location.assign(url);

/** Test hook: replace the browser navigation used on 401. */
export function setNavigate(fn: Navigate): void {
  navigate = fn;
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (UNSAFE.has(method)) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...init, method, headers, credentials: "same-origin" });
  if (response.status === 401) {
    navigate("/login");
    throw new ApiError(401, "session");
  }
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
