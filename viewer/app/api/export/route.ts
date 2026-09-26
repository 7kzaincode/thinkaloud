import path from "path";
import { NextResponse } from "next/server";
import { runEngine } from "@/lib/engine";
import { DATA_DIR, SAFE_ID, sessionDir } from "@/lib/sessions";
import { guard } from "@/lib/guard";

export const dynamic = "force-dynamic";

/** Export selected recordings via the engine; the engine validates the bundle before returning. */
export async function POST(req: Request) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const body = await req.json().catch(() => null);
  const ids: string[] = Array.isArray(body?.ids) ? body.ids.filter((x: unknown) => typeof x === "string" && SAFE_ID.test(x)) : [];
  const formats: string[] = Array.isArray(body?.formats) ? body.formats.filter((f: unknown) => f === "dataset" || f === "claude") : [];
  if (!ids.length) return NextResponse.json({ ok: false, error: "no recordings selected" }, { status: 400 });
  if (!formats.length) return NextResponse.json({ ok: false, error: "no format selected" }, { status: 400 });
  const dirs: string[] = [];
  for (const id of ids) {
    const d = await sessionDir(id);
    if (!d) return NextResponse.json({ ok: false, error: `unknown recording ${id}` }, { status: 404 });
    dirs.push(d);
  }
  const out = path.join(/*turbopackIgnore: true*/ DATA_DIR, "exports");
  const args = ["export", ...dirs, "--out", out, "--formats", formats.join(",")];
  if (body?.include_media) args.push("--include-media");
  // the user confirmed specific recordings after seeing their privacy flags; only those are let through
  const allowIds: string[] = Array.isArray(body?.allow_privacy_ids)
    ? body.allow_privacy_ids.filter((x: unknown) => typeof x === "string" && SAFE_ID.test(x) && ids.includes(x)) : [];
  for (const id of allowIds) args.push("--allow-privacy-for", id);
  const r = await runEngine(args, undefined, 600_000);
  const j = (r.json ?? null) as Record<string, unknown> | null;
  if (!j) return NextResponse.json({ ok: false, error: `export engine failed (exit ${r.code}): ${r.stderr.slice(-400)}` }, { status: 500 });
  if (j.ok && typeof j.zip === "string") j.download = `/api/exports/${encodeURIComponent(path.basename(j.zip))}`;
  return NextResponse.json(j, { status: 200 }); // ok:false with reasons is an answer, not an HTTP error
}
