import { NextResponse } from "next/server";
import { guard } from "@/lib/guard";
import { purgeTrash, restoreTrash } from "@/lib/sessions";

type Ctx = { params: Promise<{ name: string }> };

/** Restore a recording from Recently deleted. Body: {"action": "restore"} */
export async function POST(req: Request, { params }: Ctx) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const body = await req.json().catch(() => null);
  if (body?.action !== "restore") return NextResponse.json({ error: "unknown action" }, { status: 400 });
  const r = await restoreTrash((await params).name);
  return r.ok ? NextResponse.json(r) : NextResponse.json({ error: r.error }, { status: r.status });
}

/** Delete one recording permanently. */
export async function DELETE(req: Request, { params }: Ctx) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const n = await purgeTrash((await params).name);
  return NextResponse.json({ ok: n > 0 }, { status: n > 0 ? 200 : 404 });
}
