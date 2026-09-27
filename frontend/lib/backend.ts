// Server-side reads from the backend's keyless API for the public pages.
//
// Server only: this module reads INTERNAL_HIT_SECRET, which must never reach
// the browser. It is imported only by server components, route handlers and
// proxy.ts, never by a "use client" file. The bearer marks the frontend's own
// renders so the backend exempts them from its per-client rate limit.

import { LruCache } from "@/lib/lru";
import type { ApiError } from "@/lib/public-types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8102").replace(/\/+$/, "");

/** Matches the backend's own Cache-Control max-age for the dataset. */
export const REVALIDATE_SECONDS = 600;

const TIMEOUT_MS = 10_000;

export function backendHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "User-Agent": "GoBuga-frontend",
  };
  const secret = process.env.INTERNAL_HIT_SECRET;
  if (secret) headers.Authorization = `Bearer ${secret}`;
  return headers;
}

/** The backend failed or could not be reached; the page shows an error, not wrong facts. */
export class BackendUnavailable extends Error {}

export interface BackendResult<T> {
  status: number;
  body: T | null;          // the parsed body of a 2xx response
  error: ApiError | null;  // the error envelope of a 4xx response
}

async function read<T>(path: string, res: Response): Promise<BackendResult<T>> {
  if (res.status >= 500) throw new BackendUnavailable(`GET ${path}: ${res.status}`);
  const json: unknown = await res.json().catch(() => null);
  if (res.ok) {
    if (json === null) throw new BackendUnavailable(`GET ${path}: body is not JSON`);
    return { status: res.status, body: json as T, error: null };
  }
  const error = json && typeof json === "object" && "error" in json ? (json as ApiError) : null;
  return { status: res.status, body: null, error };
}

/**
 * A backend GET whose URL the site controls (a grant, the funders, the
 * taxonomy). Kept in Next's data cache and revalidated every ten minutes, so
 * a crawler burst does not reach the backend on every hit. Next caches only
 * 200 responses.
 */
export async function getCached<T>(path: string, revalidate = REVALIDATE_SECONDS): Promise<BackendResult<T>> {
  const res = await fetch(API_URL + path, {
    headers: backendHeaders(),
    next: { revalidate },
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  return read<T>(path, res);
}

// Whether a grant ID or funder slug exists, for proxy.ts (see Lookup in
// lib/public-routes.ts). Bounded, and forgotten after ten minutes, so a new
// grant is found soon after a publish.
const existence = new LruCache<boolean>(5000, REVALIDATE_SECONDS * 1000);

/**
 * True if the backend has this grant (any status, stale included), funder
 * or stats month; false if it answers 404 or 400. Fails open: if the backend
 * cannot be asked, the page itself decides.
 */
export async function exists(kind: "grant" | "funder" | "month", key: string): Promise<boolean> {
  const cacheKey = `${kind}:${key}`;
  const known = existence.get(cacheKey);
  if (known !== undefined) return known;
  const path = {
    grant: `/api/v1/opportunities/${key}`,
    funder: `/api/v1/funders/${key}`,
    month: `/api/v1/stats/${key}`,
  }[kind];
  try {
    const res = await fetch(API_URL + path, {
      method: "HEAD",
      headers: backendHeaders(),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (res.status === 404 || res.status === 400) {
      existence.set(cacheKey, false);
      return false;
    }
    if (res.ok) {
      existence.set(cacheKey, true);
      return true;
    }
  } catch {
    // Fall through: fail open.
  }
  return true;
}

// See lib/lru.ts for why visitor-chosen URLs are not put in Next's data cache.
const queryCache = new LruCache<BackendResult<unknown>>(200, REVALIDATE_SECONDS * 1000);

/**
 * A backend GET whose query the visitor chooses (/fit, the filtered index).
 * Held in a bounded in-memory cache instead of Next's data cache. A 400 is
 * cached too, so repeating a bad URL does not reach the backend either.
 */
export async function getByQuery<T>(path: string): Promise<BackendResult<T>> {
  const hit = queryCache.get(path);
  if (hit) return hit as BackendResult<T>;
  const res = await fetch(API_URL + path, {
    headers: backendHeaders(),
    cache: "no-store",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const result = await read<T>(path, res);
  if (result.status === 200 || result.status === 400) queryCache.set(path, result);
  return result;
}
