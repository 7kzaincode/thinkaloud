import { NextResponse, type NextRequest } from "next/server";
import { crossSite, localHost } from "./lib/access";

/**
 * Every request (pages, API, media, static files): the viewer serves private recordings, so it
 * only answers requests addressed to this machine by name (blocks DNS rebinding) and never
 * requests made by pages on other sites. API routes additionally check Origin and content type
 * (lib/guard.ts).
 */
export function proxy(req: NextRequest) {
  if (!localHost(req.headers.get("host"))) {
    return new NextResponse("forbidden: not a local request", { status: 403 });
  }
  if (crossSite(req.headers.get("sec-fetch-site"))) {
    return new NextResponse("forbidden: cross-site request", { status: 403 });
  }
  return NextResponse.next();
}
