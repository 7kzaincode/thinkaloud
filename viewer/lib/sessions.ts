import "server-only";
import { createHash } from "crypto";
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
export const SAFE_ID = /^[\w.-]+$/;

async function exists(p: string) {
  return fs.access(p).then(() => true, () => false);
}

async function readJson<T>(p: string): Promise<T> {
  return JSON.parse(await fs.readFile(p, "utf-8")) as T;
}

async function writeJsonAtomic(p: string, data: unknown) {
  const tmp = `${p}.${process.pid}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(data, null, 2), "utf-8");
  await fs.rename(tmp, p);
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
        schema_version: null, legacy: false, metrics: null, reviewed: false, review_stale: false,
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

export class ConflictError extends Error {}

/** Reviews never overwrite the processor's trajectory.json. A review made against an
 *  older processor output is refused (409) so the reviewer reloads the rebased version. */
export async function saveReview(id: string, t: Trajectory) {
  const dir = await sessionDir(id);
  if (!dir) return false;
  if (!Array.isArray(t?.steps) || t.session_id === undefined || typeof t.review !== "object") throw new Error("not a trajectory");
  const orig = path.join(/*turbopackIgnore: true*/ dir, ORIGINAL);
  const current = await fileHash(orig);
  if (t.review.base_hash && t.review.base_hash !== current) throw new ConflictError("the recording was reprocessed; reload to continue");
  t.review.base_hash = current;
  await writeJsonAtomic(path.join(/*turbopackIgnore: true*/ dir, REVIEWED), t);
  return true;
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
  if (!dir || name !== "playback.mp4") return null;
  const p = path.join(/*turbopackIgnore: true*/ dir, name);
  return (await exists(p)) ? p : null;
}
