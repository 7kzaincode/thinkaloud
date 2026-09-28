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
        outcome: null, warnings: [], readonly: root !== ROOTS[0], in_progress: false,
      };
      try {
        if (hasTraj) {
          const eff = await effectiveTrajectory(dir);
          const t = eff!.trajectory;
          Object.assign(summary, {
            task: t.task, criteria: t.success_criteria, recorded_at: t.recorded_at, duration_s: t.duration_s, schema_version: t.schema_version,
            legacy: t.source?.legacy ?? t.schema_version === "0.1", metrics: computeMetrics(t),
            reviewed: eff!.reviewed, outcome: t.review.outcome,
          });
          if (t.review.rebased?.dropped.length) summary.warnings.push(`${t.review.rebased.dropped.length} review edit(s) no longer match after reprocessing`);
          if (t.media?.status === "failed") summary.warnings.push(`playback failed: ${t.media.error}`);
          for (const f of t.session_flags) if (f.severity !== "info") summary.warnings.push(f.code.replaceAll("_", " "));
        } else {
          const meta = await readJson<Record<string, unknown>>(path.join(/*turbopackIgnore: true*/ dir, "meta.json")).catch(() => null);
          if (meta) Object.assign(summary, { task: String(meta.task ?? ""), criteria: String(meta.success_criteria ?? ""), recorded_at: (meta.started_at as string) ?? null, duration_s: (meta.duration_s as number) ?? null });
          else if (await beingRecorded(dir)) summary.in_progress = true;
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

// ---------- managing recordings (edit details, delete, restore) ------------------------------

/** Queued or running with a live heartbeat, or locked by a live processor (desktop app or batch job). */
export async function processingInFlight(dir: string): Promise<boolean> {
  const fresh = (ms: number) => Date.now() - ms <= 30_000;
  try {
    if (fresh((await fs.stat(path.join(/*turbopackIgnore: true*/ dir, "processing.lock"))).mtimeMs)) return true;
  } catch { /* no lock */ }
  try {
    const st = JSON.parse(await fs.readFile(path.join(/*turbopackIgnore: true*/ dir, "processing.json"), "utf-8"));
    return (st.state === "queued" || st.state === "running") && fresh(Date.parse(st.heartbeat_at ?? 0));
  } catch {
    return false;
  }
}

/** The recorder writes meta.json when it stops; until then events.jsonl keeps growing. */
export async function beingRecorded(dir: string): Promise<boolean> {
  if (await exists(path.join(/*turbopackIgnore: true*/ dir, "meta.json"))) return false;
  try {
    return Date.now() - (await fs.stat(path.join(/*turbopackIgnore: true*/ dir, "events.jsonl"))).mtimeMs < 60_000;
  } catch {
    return false;
  }
}

/** A recording the user owns (in the first folder), not a bundled sample. */
export async function ownSessionDir(id: string): Promise<string | null> {
  const dir = await sessionDir(id);
  return dir && path.dirname(dir) === ROOTS[0] ? dir : null;
}

export const TRASH_DIR = () => path.join(/*turbopackIgnore: true*/ DATA_DIR, "trash");
const DELETED_INFO = ".deleted.json";
const TRASH_SUFFIX = /__d\d+$/;

async function renameRetry(from: string, to: string) {
  // Windows refuses to move a folder while another program has a file in it open; that is
  // often momentary (antivirus, the thumbnail cache), so retry for a couple of seconds
  for (let i = 0; ; i++) {
    try {
      await fs.rename(from, to);
      return;
    } catch (e) {
      const code = (e as NodeJS.ErrnoException).code ?? "";
      if (i >= 20 || !["EPERM", "EBUSY", "EACCES"].includes(code)) throw e;
      await new Promise((r) => setTimeout(r, 100));
    }
  }
}

/** Move recordings to DATA_DIR/trash ("Recently deleted"), with their reviews. */
export async function trashSessions(ids: string[]): Promise<{ moved: string[]; refused: { id: string; reason: string }[] }> {
  const moved: string[] = [];
  const refused: { id: string; reason: string }[] = [];
  await fs.mkdir(TRASH_DIR(), { recursive: true });
  for (const id of ids) {
    const dir = await sessionDir(id);
    if (!dir) { refused.push({ id, reason: "not found" }); continue; }
    if (path.dirname(dir) !== ROOTS[0]) { refused.push({ id, reason: "sample recordings can't be deleted" }); continue; }
    if (await beingRecorded(dir)) { refused.push({ id, reason: "it is still being recorded" }); continue; }
    if (await processingInFlight(dir)) { refused.push({ id, reason: "it is being processed; try again when that has finished" }); continue; }
    let task = "";
    for (const f of [ORIGINAL, "meta.json"]) {
      const j = await readJson<{ task?: string }>(path.join(/*turbopackIgnore: true*/ dir, f)).catch(() => null);
      if (j?.task) { task = j.task; break; }
    }
    let name = id;
    if (await exists(path.join(/*turbopackIgnore: true*/ TRASH_DIR(), name))) name = `${id}__d${Date.now()}`;
    const dest = path.join(/*turbopackIgnore: true*/ TRASH_DIR(), name);
    try {
      await renameRetry(dir, dest);
    } catch (e) {
      const code = (e as NodeJS.ErrnoException).code ?? "";
      refused.push({ id, reason: ["EPERM", "EBUSY", "EACCES"].includes(code)
        ? "a file in it is open in another program (close its replay or folder and try again)" : String(e).slice(0, 200) });
      continue;
    }
    await fs.writeFile(path.join(/*turbopackIgnore: true*/ dest, DELETED_INFO),
      JSON.stringify({ id, task, deleted_at: new Date().toISOString() }, null, 2)).catch(() => {});
    moved.push(id);
  }
  return { moved, refused };
}

export interface TrashItem { name: string; id: string; task: string; deleted_at: string | null }

export async function listTrash(): Promise<TrashItem[]> {
  const out: TrashItem[] = [];
  for (const name of await fs.readdir(TRASH_DIR()).catch(() => [] as string[])) {
    if (!SAFE_ID.test(name)) continue;
    const dir = path.join(/*turbopackIgnore: true*/ TRASH_DIR(), name);
    if (!(await fs.stat(dir).then((st) => st.isDirectory(), () => false))) continue;
    const info = await readJson<{ id?: string; task?: string; deleted_at?: string }>(path.join(/*turbopackIgnore: true*/ dir, DELETED_INFO)).catch(() => null);
    out.push({ name, id: info?.id ?? name.replace(TRASH_SUFFIX, ""), task: info?.task ?? "", deleted_at: info?.deleted_at ?? null });
  }
  return out.sort((a, b) => (b.deleted_at ?? "").localeCompare(a.deleted_at ?? ""));
}

function trashItemDir(name: string): string | null {
  if (!SAFE_ID.test(name)) return null;
  const root = path.resolve(/*turbopackIgnore: true*/ TRASH_DIR());
  const dir = path.resolve(/*turbopackIgnore: true*/ root, name);
  return path.dirname(dir) === root ? dir : null;
}

export async function restoreTrash(name: string): Promise<{ ok: true; id: string } | { ok: false; error: string; status: number }> {
  const dir = trashItemDir(name);
  if (!dir || !(await exists(dir))) return { ok: false, error: "not found", status: 404 };
  const info = await readJson<{ id?: string }>(path.join(/*turbopackIgnore: true*/ dir, DELETED_INFO)).catch(() => null);
  const id = info?.id && SAFE_ID.test(info.id) ? info.id : name.replace(TRASH_SUFFIX, "");
  const dest = path.join(/*turbopackIgnore: true*/ ROOTS[0], id);
  if (await exists(dest)) return { ok: false, error: `a recording named ${id} already exists`, status: 409 };
  await fs.mkdir(ROOTS[0], { recursive: true });
  try {
    await renameRetry(dir, dest);
  } catch (e) {
    return { ok: false, error: String(e).slice(0, 200), status: 503 };
  }
  await fs.rm(path.join(/*turbopackIgnore: true*/ dest, DELETED_INFO), { force: true });
  return { ok: true, id };
}

/** Permanently delete one item from Recently deleted, or all of them (name null). */
export async function purgeTrash(name: string | null): Promise<number> {
  const names = name === null ? (await listTrash()).map((i) => i.name) : [name];
  let n = 0;
  for (const nm of names) {
    const dir = trashItemDir(nm);
    if (!dir || !(await exists(dir))) continue;
    await fs.rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    n++;
  }
  return n;
}
