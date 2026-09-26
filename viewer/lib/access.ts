// Shared by proxy.ts (every request) and guard.ts (API routes). No server-only imports:
// proxy.ts runs before routing.

export const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

/** The Host header names this machine (so a DNS-rebinding page on another name gets nothing). */
export function localHost(hostHeader: string | null): boolean {
  return LOOPBACK.has((hostHeader ?? "").replace(/:\d+$/, "").toLowerCase());
}

/**
 * The browser says the request was made by a page on another site (an <img>/<video>/fetch
 * from elsewhere). Requests typed into the address bar ("none") and the viewer's own pages
 * ("same-origin") pass; clients that don't send the header (curl, the engine) pass too.
 */
export function crossSite(secFetchSite: string | null): boolean {
  return secFetchSite === "cross-site" || secFetchSite === "same-site";
}

/** A link clicked on another site that opens a viewer page in the tab: the other site can't read it. */
export function topLevelPageVisit(req: { method: string; headers: Headers }, pathname: string): boolean {
  return req.method === "GET" && !pathname.startsWith("/api/") && !pathname.startsWith("/_next/")
    && req.headers.get("sec-fetch-mode") === "navigate" && req.headers.get("sec-fetch-dest") === "document";
}
