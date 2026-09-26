import "server-only";
import { promises as fs } from "fs";
import path from "path";
import type { SessionSummary, Trajectory } from "./types";

// Folders that contain session folders. Override with THINKALOUD_DIRS=dir1;dir2
const ROOTS = (process.env.THINKALOUD_DIRS ?? "../sessions;../samples")
  .split(/[;,]/)
  .filter(Boolean)
  .map((d) => path.resolve(/*turbopackIgnore: true*/ process.cwd(), d));

const ORIGINAL = "trajectory.json";
const REVIEWED = "trajectory.reviewed.json";
const SAFE_ID = /^[\w.-]+$/;

async function exists(p: string) {
  return fs.access(p).then(() => true, () => false);
}

async function sessionDir(id: string): Promise<string | null> {
  if (!SAFE_ID.test(id)) return null;
  for (const root of ROOTS) {
    const dir = path.join(/*turbopackIgnore: true*/ root, id);
    if (await exists(path.join(/*turbopackIgnore: true*/ dir, ORIGINAL))) return dir;
  }
  return null;
}

async function readJson<T>(p: string): Promise<T> {
  return JSON.parse(await fs.readFile(p, "utf-8")) as T;
}

export async function listSessions(): Promise<SessionSummary[]> {
  const out: SessionSummary[] = [];
  const seen = new Set<string>();
  for (const root of ROOTS) {
    const names = await fs.readdir(root).catch(() => [] as string[]);
    for (const name of names) {
      if (seen.has(name) || !SAFE_ID.test(name)) continue;
      const dir = path.join(/*turbopackIgnore: true*/ root, name);
      if (!(await exists(path.join(/*turbopackIgnore: true*/ dir, ORIGINAL)))) continue;
      seen.add(name);
      const reviewed = await exists(path.join(/*turbopackIgnore: true*/ dir, REVIEWED));
      const t = await readJson<Trajectory>(path.join(/*turbopackIgnore: true*/ dir, reviewed ? REVIEWED : ORIGINAL));
      out.push({
        id: name,
        task: t.task,
        duration_s: t.duration_s,
        steps: t.steps.length,
        summary: t.qc.summary,
        high: t.steps.reduce((n, s) => n + s.flags.filter((f) => f.severity === "high").length, 0),
        reviewed,
        outcome: t.review?.outcome ?? null,
        recorded_at: t.recorded_at,
      });
    }
  }
  return out.sort((a, b) => (b.recorded_at ?? "").localeCompare(a.recorded_at ?? ""));
}

/** The reviewed copy if one exists, otherwise the processor's output. */
export async function getSession(id: string) {
  const dir = await sessionDir(id);
  if (!dir) return null;
  const reviewed = await exists(path.join(/*turbopackIgnore: true*/ dir, REVIEWED));
  const trajectory = await readJson<Trajectory>(path.join(/*turbopackIgnore: true*/ dir, reviewed ? REVIEWED : ORIGINAL));
  return { trajectory, reviewed };
}

/** Reviews never overwrite the processor's trajectory.json. */
export async function saveReview(id: string, t: Trajectory) {
  const dir = await sessionDir(id);
  if (!dir) return false;
  if (!Array.isArray(t?.steps) || t.session_id === undefined) throw new Error("not a trajectory");
  await fs.writeFile(path.join(/*turbopackIgnore: true*/ dir, REVIEWED), JSON.stringify(t, null, 2), "utf-8");
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
