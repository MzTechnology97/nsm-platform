/**
 * Fetch wrapper for the NSM UI API (same origin, session cookie).
 *
 * - every change sends the session CSRF token in X-CSRF-Token;
 * - 401 means the session expired: the browser goes to the sign-in page;
 * - errors carry the server message (FastAPI "detail").
 */

let csrfToken = "";

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export class ApiError extends Error {
  /** Machine-readable reason when the server sends ``detail: {code, message}``. */
  constructor(public status: number, message: string, public code?: string) {
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
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (UNSAFE.has(method)) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...init, method, headers, credentials: "same-origin" });
  if (response.status === 401) {
    navigate("/login");
    throw new ApiError(401, "session");
  }
  if (!response.ok) {
    let message = response.statusText;
    let code: string | undefined;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
      else if (typeof body?.detail?.message === "string") {
        message = body.detail.message;
        code = typeof body.detail.code === "string" ? body.detail.code : undefined;
      }
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message, code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
