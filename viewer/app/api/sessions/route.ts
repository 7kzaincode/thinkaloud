import { NextResponse } from "next/server";
import { listSessions } from "@/lib/sessions";
import { guard } from "@/lib/guard";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const denied = guard(req);
  if (denied) return denied;
  return NextResponse.json({ sessions: await listSessions() });
}
