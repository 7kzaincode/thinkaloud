// What "play this step" plays. Run: npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { stepClip } from "./clip.ts";
import type { Trajectory } from "./types.ts";

const step = (id: number, t: number, narration: [number, number][] = [], after?: [number, number]) => ({
  id, t_start: t, t_end: t, narration: narration.map(([a, b], i) => ({ segment_id: i, t_start: a, t_end: b, timing: "before_action" })),
  observations: { before: { status: "ok", file: null }, after: after ? { status: "ok", file: "a.png", t_capture_start: after[0], t_capture_end: after[1] } : { status: "missing", file: null } },
});
const traj = (steps: ReturnType<typeof step>[]) => ({ duration_s: 70.9, steps } as unknown as Trajectory);

test("a step's clip covers everything said about it, not just a second around the click", () => {
  // "This one seems to be the lowest, but it's basic economy..." (58.6-63.1), click at 63.8,
  // then "That's the one. Non-stop. Done." (66.0-67.9) after it
  const t = traj([step(16, 55.6, [[52.4, 55.2]]), step(17, 63.8, [[58.6, 63.1], [66.0, 67.9]])]);
  const c = stepClip(t, 1)!;
  assert.ok(Math.abs(c.start - 58.3) < 1e-9);
  assert.ok(Math.abs(c.end - 68.3) < 1e-9);
});

test("without narration: a second before the action to just after it, stopping before the next action", () => {
  const t = traj([step(0, 10.8), step(1, 11.6)]);
  const c = stepClip(t, 0)!;
  assert.ok(Math.abs(c.start - 9.8) < 1e-9 && Math.abs(c.end - 11.55) < 1e-9);
  const lone = traj([step(0, 20.2, [], [21.0, 22.9])]);
  assert.ok(Math.abs(stepClip(lone, 0)!.end - 23.1) < 1e-9);  // up to the after-state screenshot
});

test("the end state plays the last seconds; clips stay inside the recording", () => {
  const t = traj([step(0, 0.5, [[0.0, 0.4]]), step(1, 70.5, [], undefined)]);
  const e = stepClip(t, "end")!;
  assert.ok(Math.abs(e.start - 67.9) < 1e-9 && e.end === 70.9);
  assert.equal(stepClip(t, 0)!.start, 0);
  assert.equal(stepClip(t, 1)!.end, 70.9);
  assert.equal(stepClip(t, 5), null);
});

test("a step that carries an earlier step's reasoning plays from that sentence", () => {
  // "Non-stop only. A connection might save 50 bucks..." (44.5-47.9) -> #13 opens Stops at 48.5; #14 picks Nonstop at 49.4
  const src = step(13, 48.5, [[44.5, 47.9]]);
  const carried = { ...step(14, 49.4), reasoning_source: "carried", carried_from: 13 };
  const t = traj([src, carried as ReturnType<typeof step>, step(15, 50.7)]);
  const c = stepClip(t, 1)!;
  assert.ok(Math.abs(c.start - 44.2) < 1e-9);
  assert.ok(Math.abs(c.end - 50.65) < 1e-9);   // still stops before the next action
  const far = { ...step(14, 70.0), reasoning_source: "carried", carried_from: 13 };
  assert.ok(Math.abs(stepClip(traj([src, far as ReturnType<typeof step>]), 1)!.start - 69.0) < 1e-9);  // too long ago
});
