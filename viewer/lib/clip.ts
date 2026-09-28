import type { Trajectory } from "./types.ts";

/**
 * The stretch of the recording that belongs to one step: from its first narration (or a
 * second before the action) to the end of its narration (or just after the after-state).
 * Replaying a step plays exactly this, and the timeline shades it.
 */
export function stepClip(t: Trajectory, s: number | "end", mediaDuration?: number | null): { start: number; end: number } | null {
  const dur = t.duration_s ?? mediaDuration ?? null;
  if (s === "end") return dur ? { start: Math.max(0, dur - 3), end: dur } : null;
  const st = t.steps[s];
  if (!st) return null;
  let end = st.t_end + 1.5;
  const after = st.observations?.after;
  if (after?.file && after.t_capture_end != null && after.t_capture_end > end && after.t_capture_end <= st.t_end + 4) {
    end = after.t_capture_end + 0.2;  // show the screen the after-state was taken from
  }
  const next = t.steps[s + 1];
  if (next && end > next.t_start - 0.05) end = Math.max(st.t_end + 0.3, next.t_start - 0.05);
  let start = st.t_start - 1;
  // the whole narration attached to this step, whether said before, during or after the action;
  // a step that carries an earlier step's reasoning plays from that sentence (said up to 15 s before)
  let own = st.narration ?? [];
  if (!own.length && st.reasoning_source === "carried" && st.carried_from != null) {
    const src = t.steps.find((x) => x.id === st.carried_from);
    const said = (src?.narration ?? []).filter((n) => n.timing !== "after_action");
    if (said.length && st.t_start - Math.min(...said.map((n) => n.t_start)) <= 15) own = said.map((n) => ({ ...n, t_end: n.t_start }));
  }
  if (own.length) {
    start = Math.min(start, Math.max(st.t_start - 20, Math.min(...own.map((n) => n.t_start)) - 0.3));
    end = Math.max(end, Math.min(st.t_end + 20, Math.max(...own.map((n) => n.t_end)) + 0.4));
  }
  if (dur) end = Math.min(end, dur);
  return { start: Math.max(0, start), end };
}
