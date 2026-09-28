import { NextResponse } from "next/server";
import { guard } from "@/lib/guard";
import { runEngine } from "@/lib/engine";
import { beingRecorded, ownSessionDir, sessionDir } from "@/lib/sessions";

type Ctx = { params: Promise<{ id: string }> };

/** Change the task / "done when" text. Body: {"task": "...", "success_criteria": "..."} */
export async function POST(req: Request, { params }: Ctx) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const id = (await params).id;
  const dir = await ownSessionDir(id);
  if (!dir) {
    return (await sessionDir(id))
      ? NextResponse.json({ error: "sample recordings can't be edited" }, { status: 403 })
      : NextResponse.json({ error: "not found" }, { status: 404 });
  }
  if (await beingRecorded(dir)) return NextResponse.json({ error: "this recording is still being recorded" }, { status: 409 });
  const body = await req.json().catch(() => null);
  const task = typeof body?.task === "string" ? body.task : null;
  const criteria = typeof body?.success_criteria === "string" ? body.success_criteria : null;
  if (task === null && criteria === null) return NextResponse.json({ error: "nothing to change" }, { status: 400 });
  const r = await runEngine(["details", dir], { task, success_criteria: criteria }, 60_000);
  const j = r.json as { ok?: boolean; error?: string } | null;
  if (!j) return NextResponse.json({ error: `the engine failed (${r.code}): ${r.stderr.slice(-300)}` }, { status: 500 });
  return NextResponse.json(j, { status: j.ok ? 200 : 409 });
}
