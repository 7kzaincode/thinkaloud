import { randomBytes } from "crypto";
import { promises as fs } from "fs";
import path from "path";
import { NextResponse } from "next/server";
import { startEngine } from "@/lib/engine";
import { DATA_DIR, SAFE_ID, processingInFlight, sessionDir } from "@/lib/sessions";
import { speechModel } from "@/lib/speech";
import { guard } from "@/lib/guard";

export const dynamic = "force-dynamic";
const JOBS = () => path.join(/*turbopackIgnore: true*/ DATA_DIR, "jobs");

/** Start a batch job (native engine). Docker runs the same `batch` command; see README. */
export async function POST(req: Request) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const body = await req.json().catch(() => null);
  const ids: string[] = Array.isArray(body?.ids) ? body.ids.filter((x: unknown) => typeof x === "string" && SAFE_ID.test(x)) : [];
  if (!ids.length) return NextResponse.json({ error: "no recordings selected" }, { status: 400 });
  const concurrency = Math.min(4, Math.max(1, Number(body?.concurrency) || 2));
  const dirs: string[] = [];
  const busy: string[] = [];
  for (const id of ids) {
    const d = await sessionDir(id);
    if (!d) continue;
    if (await processingInFlight(d)) busy.push(id);
    else dirs.push(d);
  }
  if (!dirs.length) {
    return busy.length
      ? NextResponse.json({ error: "already being processed", busy }, { status: 409 })
      : NextResponse.json({ error: "none of the recordings exist" }, { status: 404 });
  }
  await fs.mkdir(JOBS(), { recursive: true });
  const jobId = `b${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}-${randomBytes(2).toString("hex")}`;
  const args = ["batch", ...dirs, "--jobs", JOBS(), "--concurrency", String(concurrency), "--job-id", jobId, "--runner", "local",
    "--model", (await speechModel()).id];
  if (body?.force) args.push("--force");
  const started = await startEngine(args);
  if ("error" in started) return NextResponse.json({ error: started.error }, { status: 500 });
  return NextResponse.json({ job_id: jobId, pid: started.pid, busy });
}

export async function GET(req: Request) {
  const denied = guard(req);
  if (denied) return denied;
  const names = await fs.readdir(JOBS()).catch(() => [] as string[]);
  const jobs = [];
  for (const n of names.filter((n) => n.endsWith(".json"))) {
    try {
      const j = JSON.parse(await fs.readFile(path.join(/*turbopackIgnore: true*/ JOBS(), n), "utf-8"));
      const age = (Date.now() - Date.parse(j.heartbeat_at ?? 0)) / 1000;
      if (j.state === "running" && age > 30) j.state = "interrupted";
      delete j.sessions;
      jobs.push(j);
    } catch {
      /* partially written; skip */
    }
  }
  jobs.sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
  return NextResponse.json({ jobs: jobs.slice(0, 10) });
}
