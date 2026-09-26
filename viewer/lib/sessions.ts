import "server-only";
import { createHash, randomBytes } from "crypto";
import { promises as fs } from "fs";
import path from "path";
import { computeMetrics } from "./metrics.ts";
import { rebaseReview } from "./review.ts";
import type { ProcessingStatus, SessionSummary, Trajectory } from "./types.ts";

// Folders that contain session folders. Override with THINKALOUD_DIRS=dir1;dir2
// The first folder is where new recordings go; exports/ and jobs/ live next to it.
export const ROOTS = (process.env.THINKALOUD_DIRS ?? "../sessions;../samples")
  .split(/[;,]/)
  .filter(Boolean)
  .map((d) => path.resolve(/*turbopackIgnore: true*/ process.cwd(), d));
export const DATA_DIR = process.env.THINKALOUD_DATA
  ? path.resolve(/*turbopackIgnore: true*/ process.env.THINKALOUD_DATA)
  : path.dirname(ROOTS[0]);

const ORIGINAL = "trajectory.json";
const REVIEWED = "trajectory.reviewed.json";
const STATUS = "processing.json";
export const SAFE_ID = /^(?!\.{1,2}$)[\w.-]+$/;

async function exists(p: string) {
  return fs.access(p).then(() => true, () => false);
}

async function readJson<T>(p: string): Promise<T> {
  return JSON.parse(await fs.readFile(p, "utf-8")) as T;
}

async function writeJsonAtomic(p: string, data: unknown) {
  const tmp = `${p}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;  // unique per write
  await fs.writeFile(tmp, JSON.stringify(data, null, 2), "utf-8");
  try {
    // on Windows a rename fails while another process (antivirus, the export engine) briefly has
    // the destination open: retry for a few seconds, like fsutil.replace_retry on the Python side
    for (let i = 0; ; i++) {
      try {
        await fs.rename(tmp, p);
        return;
      } catch (e) {
        const code = (e as NodeJS.ErrnoException).code;
        if (i >= 40 || !["EPERM", "EBUSY", "EACCES"].includes(code ?? "")) throw e;
        await new Promise((r) => setTimeout(r, 50 * (1 + Math.floor(i / 10))));
      }
    }
  } finally {
    await fs.rm(tmp, { force: true }).catch(() => {});
  }
}

export async function sessionDir(id: string): Promise<string | null> {
  if (!SAFE_ID.test(id)) return null;
  for (const root of ROOTS) {
    const dir = path.join(/*turbopackIgnore: true*/ root, id);
    if ((await exists(path.join(/*turbopackIgnore: true*/ dir, ORIGINAL))) ||
        (await exists(path.join(/*turbopackIgnore: true*/ dir, "events.jsonl")))) return dir;
  }
  return null;
}

async function fileHash(p: string): Promise<string> {
  return createHash("sha256").update(await fs.readFile(p)).digest("hex").slice(0, 16);
}

/** Older trajectories (schema 0.1) have no observations; derive honest ones for display. */
export function normalize(t: Trajectory): Trajectory {
  for (const s of t.steps) {
    if (!s.observations) {
      s.observations = {
        before: s.screenshot
          ? { status: "at_action", file: s.screenshot, source: "still", reason: "schema 0.1: captured when the action happened" }
          : { status: "missing", file: null, reason: "schema 0.1: no screenshot" },
        after: { status: "missing", file: null, reason: "schema 0.1: after-states were not captured" },
      };
    }
  }
  t.final_observation ??= t.final_screenshot
    ? { status: "ok", file: t.final_screenshot, source: "still" }
    : { status: "missing", file: null };
  return t;
}

/**
 * The trajectory a reviewer should see: their review if it matches the current
 * processor output, a rebased review if the output changed, else the processor output.
 */
export async function effectiveTrajectory(dir: string): Promise<{ trajectory: Trajectory; reviewed: boolean; rebased: boolean; baseHash: string } | null> {
  const orig = path.join(/*turbopackIgnore: true*/ dir, ORIGINAL);
  if (!(await exists(orig))) return null;
  const baseHash = await fileHash(orig);
  const revPath = path.join(/*turbopackIgnore: true*/ dir, REVIEWED);
  const fresh = normalize(await readJson<Trajectory>(orig));
  if (!(await exists(revPath))) {
    fresh.review.base_hash = baseHash;
    return { trajectory: fresh, reviewed: false, rebased: false, baseHash };
  }
  const rev = normalize(await readJson<Trajectory>(revPath));
  if (rev.review.base_hash === baseHash) return { trajectory: rev, reviewed: true, rebased: false, baseHash };
  const merged = rebaseReview(fresh, rev, baseHash);
  await writeJsonAtomic(revPath, merged);
  return { trajectory: merged, reviewed: true, rebased: true, baseHash };
}

export async function processingStatus(dir: string): Promise<ProcessingStatus | null> {
  const p = path.join(/*turbopackIgnore: true*/ dir, STATUS);
  const st = (await exists(p)) ? await readJson<ProcessingStatus & { heartbeat_at?: string }>(p).catch(() => null) : null;
  // a job that stopped heart-beating died mid-run (crash, app closed, container stopped)
  if (st && (st.state === "running" || st.state === "queued") && Date.now() - Date.parse(st.heartbeat_at ?? "") > 30_000) {
    return { ...st, state: "interrupted", error: st.error ?? "the processing job stopped before finishing" };
  }
  return st;
}

export async function listSessions(): Promise<SessionSummary[]> {
  const out: SessionSummary[] = [];
  const seen = new Set<string>();
  for (const root of ROOTS) {
    const names = await fs.readdir(root).catch(() => [] as string[]);
    for (const name of names) {
      if (seen.has(name) || !SAFE_ID.test(name)) continue;
      const dir = path.join(/*turbopackIgnore: true*/ root, name);
      const hasEvents = await exists(path.join(/*turbopackIgnore: true*/ dir, "events.jsonl"));
      const hasTraj = await exists(path.join(/*turbopackIgnore: true*/ dir, ORIGINAL));
      if (!hasEvents && !hasTraj) continue;
      seen.add(name);
      const processing = await processingStatus(dir);
      const summary: SessionSummary = {
        id: name, task: "", recorded_at: null, duration_s: null, processed: hasTraj, processing,
        schema_version: null, legacy: false, metrics: null, reviewed: false,
        outcome: null, warnings: [],
      };
      try {
        if (hasTraj) {
          const eff = await effectiveTrajectory(dir);
          const t = eff!.trajectory;
          Object.assign(summary, {
            task: t.task, recorded_at: t.recorded_at, duration_s: t.duration_s, schema_version: t.schema_version,
            legacy: t.source?.legacy ?? t.schema_version === "0.1", metrics: computeMetrics(t),
            reviewed: eff!.reviewed, outcome: t.review.outcome,
          });
          if (t.review.rebased?.dropped.length) summary.warnings.push(`${t.review.rebased.dropped.length} review edit(s) no longer match after reprocessing`);
          if (t.media?.status === "failed") summary.warnings.push(`playback failed: ${t.media.error}`);
          for (const f of t.session_flags) if (f.severity !== "info") summary.warnings.push(f.code.replaceAll("_", " "));
        } else {
          const meta = await readJson<Record<string, unknown>>(path.join(/*turbopackIgnore: true*/ dir, "meta.json")).catch(() => null);
          if (meta) Object.assign(summary, { task: String(meta.task ?? ""), recorded_at: (meta.started_at as string) ?? null, duration_s: (meta.duration_s as number) ?? null });
          else summary.warnings.push("meta.json missing or unreadable");
        }
        if (processing?.state === "failed") summary.warnings.push(`processing failed: ${processing.error ?? "unknown error"}`);
      } catch (e) {
        summary.error = `could not read: ${String(e).slice(0, 200)}`;
      }
      out.push(summary);
    }
  }
  return out.sort((a, b) => (b.recorded_at ?? "").localeCompare(a.recorded_at ?? ""));
}

export async function getSession(id: string) {
  const dir = await sessionDir(id);
  if (!dir) return null;
  return effectiveTrajectory(dir);
}

/**
 * Save a review. The body comes from the browser, so it is never stored as-is: only
 * review-owned fields (reasoning edits, flag decisions, checklist, AI suggestions and the
 * decisions on them, outcome, notes) are taken from it and laid over the processor's
 * trajectory.json. If the recording was reprocessed since the page loaded, the same merge
 * rebases the reviewer's edits onto the new steps instead of rejecting them.
 * Reviews never modify trajectory.json itself.
 */
export async function saveReview(id: string, incoming: Trajectory):
    Promise<{ ok: false } | { ok: true; rebased: boolean; trajectory: Trajectory; base_hash: string }> {
  const dir = await sessionDir(id);
  if (!dir) return { ok: false };
  if (!Array.isArray(incoming?.steps) || !incoming.review || typeof incoming.review !== "object") throw new Error("not a trajectory");
  const orig = path.join(/*turbopackIgnore: true*/ dir, ORIGINAL);
  // hash and parse the same bytes: a reprocess between two reads would pair a hash with other content
  const bytes = await fs.readFile(orig);
  const current = createHash("sha256").update(bytes).digest("hex").slice(0, 16);
  const fresh = normalize(JSON.parse(bytes.toString("utf-8")) as Trajectory);
  const sameBase = incoming.review.base_hash === current;
  const merged = rebaseReview(fresh, incoming, current, { record: !sameBase });
  if (sameBase) {
    // a page that hasn't adopted the rebased copy yet doesn't know about the rebase record the
    // previous save wrote: keep it (what was dropped must stay on record and in exports)
    const prev = await readJson<Trajectory>(path.join(/*turbopackIgnore: true*/ dir, REVIEWED)).catch(() => null);
    const kept = prev?.review?.rebase_history ?? [];
    if (kept.length > (merged.review.rebase_history?.length ?? 0)) {
      merged.review.rebase_history = kept;
      merged.review.rebased = prev!.review.rebased;
    }
  }
  await writeJsonAtomic(path.join(/*turbopackIgnore: true*/ dir, REVIEWED), merged);
  return { ok: true, rebased: !sameBase, trajectory: merged, base_hash: current };
}

export async function discardReview(id: string) {
  const dir = await sessionDir(id);
  if (!dir) return false;
  await fs.rm(path.join(/*turbopackIgnore: true*/ dir, REVIEWED), { force: true });
  return true;
}

/** Resolve a frame path inside the session folder, refusing anything outside it. */
export async function framePath(id: string, rel: string): Promise<string | null> {
  const dir = await sessionDir(id);
  if (!dir || !/^frames\/[\w.-]+\.png$/.test(rel)) return null;
  const p = path.resolve(/*turbopackIgnore: true*/ dir, rel);
  return p.startsWith(dir + path.sep) && (await exists(p)) ? p : null;
}

export async function mediaPath(id: string, name: string): Promise<string | null> {
  const dir = await sessionDir(id);
  if (!dir || !/^playback(-[0-9a-f]{8})?\.mp4$/.test(name)) return null;
  const p = path.join(/*turbopackIgnore: true*/ dir, name);
  return (await exists(p)) ? p : null;
}
