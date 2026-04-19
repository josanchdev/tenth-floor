/**
 * Tiny typed fetch wrapper for the FastAPI backend.
 *
 * The Vite dev server proxies `/api/*` to `http://127.0.0.1:8765/*` (see
 * vite.config.ts), so the dashboard talks to the API via relative URLs
 * and never needs to know the backend port.
 */

const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

type QueryParams = Record<string, string | number | boolean | undefined>;

async function request<T>(
  path: string,
  init?: RequestInit & { params?: QueryParams },
): Promise<T> {
  const { params, ...rest } = init ?? {};
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }

  const response = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: { "Content-Type": "application/json", ...rest.headers },
    ...rest,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const body: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : response.statusText;
    throw new ApiError(detail, response.status, body);
  }

  return body as T;
}

export const api = {
  get: <T>(path: string, params?: QueryParams) =>
    request<T>(path, { params }),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
};
