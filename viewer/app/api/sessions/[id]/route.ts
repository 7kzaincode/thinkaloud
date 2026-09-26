import { NextResponse } from "next/server";
import { discardReview, getSession, saveReview } from "@/lib/sessions";
import type { Trajectory } from "@/lib/types";
import { guard } from "@/lib/guard";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(req: Request, { params }: Ctx) {
  const denied = guard(req);
  if (denied) return denied;
  const s = await getSession((await params).id);
  return s ? NextResponse.json(s) : NextResponse.json({ error: "not found" }, { status: 404 });
}

export async function PUT(req: Request, { params }: Ctx) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  let t: Trajectory;
  try {
    t = (await req.json()) as Trajectory;
  } catch {
    return NextResponse.json({ error: "body is not JSON" }, { status: 400 });
  }
  try {
    const r = await saveReview((await params).id, t);
    if (!r.ok) return NextResponse.json({ error: "not found" }, { status: 404 });
    // when the recording was reprocessed, the client adopts the rebased version (edits carried over)
    return NextResponse.json({ ok: true, base_hash: r.base_hash, rebased: r.rebased, trajectory: r.rebased ? r.trajectory : undefined });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 400 });
  }
}

export async function DELETE(req: Request, { params }: Ctx) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const ok = await discardReview((await params).id);
  return NextResponse.json({ ok }, { status: ok ? 200 : 404 });
}
