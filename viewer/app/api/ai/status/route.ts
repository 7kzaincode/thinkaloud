import { NextResponse } from "next/server";
import { runEngine } from "@/lib/engine";
import { guard } from "@/lib/guard";

export const dynamic = "force-dynamic";

/** Whether AI review can run. Never returns the key itself. */
export async function GET(req: Request) {
  const denied = guard(req);
  if (denied) return denied;
  const r = await runEngine(["ai-status"], undefined, 30_000);
  const j = r.json as { configured?: boolean; provider?: string; provider_name?: string; model?: string; reason?: string } | null;
  if (!j) return NextResponse.json({ configured: false, provider: "", provider_name: "", model: "", reason: `engine unavailable (${r.stderr.slice(-200) || r.code})` });
  return NextResponse.json({ configured: !!j.configured, provider: j.provider ?? "", provider_name: j.provider_name ?? "",
    model: j.model ?? "", reason: j.reason ?? null });
}
