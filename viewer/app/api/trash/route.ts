import { NextResponse } from "next/server";
import { guard } from "@/lib/guard";
import { SAFE_ID, listTrash, purgeTrash, trashSessions } from "@/lib/sessions";

export const dynamic = "force-dynamic";

/** Recently deleted recordings. */
export async function GET(req: Request) {
  const denied = guard(req);
  if (denied) return denied;
  return NextResponse.json({ items: await listTrash() });
}

/** Delete recordings: move them to Recently deleted. Body: {"ids": [...]} */
export async function POST(req: Request) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const body = await req.json().catch(() => null);
  const ids: string[] = Array.isArray(body?.ids) ? body.ids.filter((x: unknown) => typeof x === "string" && SAFE_ID.test(x)) : [];
  if (!ids.length) return NextResponse.json({ error: "no recordings selected" }, { status: 400 });
  const r = await trashSessions([...new Set(ids)]);
  return NextResponse.json(r, { status: r.moved.length || !r.refused.length ? 200 : 409 });
}

/** Empty Recently deleted (permanent). */
export async function DELETE(req: Request) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  return NextResponse.json({ ok: true, deleted: await purgeTrash(null) });
}
