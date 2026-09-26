import { NextResponse } from "next/server";
import { ConflictError, discardReview, getSession, saveReview } from "@/lib/sessions";
import type { Trajectory } from "@/lib/types";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Ctx) {
  const s = await getSession((await params).id);
  return s ? NextResponse.json(s) : NextResponse.json({ error: "not found" }, { status: 404 });
}

export async function PUT(req: Request, { params }: Ctx) {
  let t: Trajectory;
  try {
    t = (await req.json()) as Trajectory;
  } catch {
    return NextResponse.json({ error: "body is not JSON" }, { status: 400 });
  }
  try {
    const ok = await saveReview((await params).id, t);
    return ok ? NextResponse.json({ ok: true, base_hash: t.review.base_hash }) : NextResponse.json({ error: "not found" }, { status: 404 });
  } catch (e) {
    if (e instanceof ConflictError) return NextResponse.json({ error: e.message }, { status: 409 });
    return NextResponse.json({ error: String(e) }, { status: 400 });
  }
}

export async function DELETE(_req: Request, { params }: Ctx) {
  const ok = await discardReview((await params).id);
  return NextResponse.json({ ok }, { status: ok ? 200 : 404 });
}
