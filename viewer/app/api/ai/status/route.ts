import { NextResponse } from "next/server";
import { runEngine } from "@/lib/engine";

export const dynamic = "force-dynamic";

/** Whether AI review can run. Never returns the key itself. */
export async function GET() {
  const r = await runEngine(["ai-status"], undefined, 30_000);
  const j = r.json as { configured?: boolean; model?: string; reason?: string } | null;
  if (!j) return NextResponse.json({ configured: false, model: "", reason: `engine unavailable (${r.stderr.slice(-200) || r.code})` });
  return NextResponse.json({ configured: !!j.configured, model: j.model ?? "", reason: j.reason ?? null });
}
