import { NextResponse } from "next/server";
import { runEngine } from "@/lib/engine";
import { sessionDir } from "@/lib/sessions";

type Ctx = { params: Promise<{ id: string }> };
const KINDS = new Set(["narration", "checklist", "final_screen"]);

/**
 * Runs one AI check on the reviewer's current state (sent in the body, so unsaved edits count).
 * Returns suggestions only; the client applies them as pending items. The API key stays in the
 * server environment and is never part of any response.
 */
export async function POST(req: Request, { params }: Ctx) {
  const dir = await sessionDir((await params).id);
  if (!dir) return NextResponse.json({ error: "not found" }, { status: 404 });
  const body = await req.json().catch(() => null);
  if (!body || !KINDS.has(body.kind) || typeof body.trajectory !== "object") {
    return NextResponse.json({ error: "expected {kind, trajectory}", error_type: "bad_request" }, { status: 400 });
  }
  const r = await runEngine(["review", "--session", dir, "--kind", body.kind], { trajectory: body.trajectory }, 300_000);
  const j = r.json as Record<string, unknown> | null;
  if (!j) return NextResponse.json({ error: `review engine failed (exit ${r.code})`, error_type: "engine_error" }, { status: 500 });
  return NextResponse.json(j, { status: j.error ? 502 : 200 });
}
