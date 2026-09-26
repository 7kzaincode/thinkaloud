import "server-only";

/**
 * The viewer is a local tool: it serves private recordings and can spend the user's API key.
 * Every API route calls this first.
 *  - Host must be a loopback name (blocks DNS-rebinding and LAN access if it was ever bound wider).
 *  - A cross-site Origin is refused (a page on another site can't drive the API from the browser).
 *  - Writes must be JSON, so a "simple" cross-site form/text POST can't skip the CORS preflight.
 */
const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

export function guard(req: Request, opts: { write?: boolean } = {}): Response | null {
  const host = (req.headers.get("host") ?? "").replace(/:\d+$/, "").toLowerCase();
  if (!LOOPBACK.has(host)) return new Response("forbidden: not a local request", { status: 403 });
  const origin = req.headers.get("origin");
  if (origin) {
    let o: URL | null = null;
    try { o = new URL(origin); } catch { /* malformed */ }
    if (!o || !LOOPBACK.has(o.hostname.toLowerCase()) || o.host !== req.headers.get("host")) {
      return new Response("forbidden: cross-origin request", { status: 403 });
    }
  }
  if (opts.write && req.method !== "DELETE") {
    const ct = req.headers.get("content-type") ?? "";
    if (!ct.toLowerCase().startsWith("application/json")) {
      return new Response("unsupported media type: send application/json", { status: 415 });
    }
  }
  return null;
}
