/**
 * Per-session metrics for the batch view, computed from persisted data only.
 *
 * Definitions (denominators are explicit):
 *   steps                 number of steps in the trajectory
 *   narrated_steps        steps with at least one narration segment of their own
 *                         (transcript_ids non-empty). Carried or reviewer-written
 *                         reasoning does NOT count as narration.
 *   pct_narrated          narrated_steps / steps
 *   carried_steps         steps whose reasoning was carried from an earlier step
 *   reviewer_reasoning    steps whose reasoning a human wrote or edited
 *   narration_words       words in the narration segments assigned to steps
 *   avg_words_per_step    narration_words / steps   (all steps, not only narrated ones)
 *   human checklist       counts of human verdicts only; AI suggestions never count
 */
import type { Trajectory } from "./types.ts";

export interface SessionMetrics {
  steps: number;
  narrated_steps: number;
  pct_narrated: number | null;
  carried_steps: number;
  reviewer_reasoning: number;
  narration_words: number;
  avg_words_per_step: number | null;
  open_flags: { high: number; warn: number; info: number };
  dismissed_flags: number;
  missing_after: number;
  checklist: { items: number; met: number; not_met: number; unclear: number; undecided: number };
  ai_pending: number;
}

const words = (s: string) => (s.trim() ? s.trim().split(/\s+/).length : 0);

export function computeMetrics(t: Trajectory): SessionMetrics {
  const segText = new Map(t.transcript.map((g) => [g.id, g.text]));
  const steps = t.steps.length;
  let narrated = 0, carried = 0, reviewer = 0, nw = 0, missingAfter = 0, dismissed = 0;
  const open = { high: 0, warn: 0, info: 0 };
  for (const s of t.steps) {
    if (s.transcript_ids.length) narrated++;
    for (const id of s.transcript_ids) nw += words(segText.get(id) ?? "");
    if (s.reasoning_source === "carried") carried++;
    if (s.reasoning_source === "reviewer") reviewer++;
    if (s.observations?.after?.status === "missing") missingAfter++;
    for (const f of s.flags) open[f.severity]++;
    dismissed += s.dismissed_flags?.length ?? 0;
  }
  const items = t.review.checklist ?? [];
  const cl = { items: items.length, met: 0, not_met: 0, unclear: 0, undecided: 0 };
  for (const it of items) {
    // a verdict given for earlier wording is not a decision about the item as it reads now
    if (it.human_verdict && it.verdict_text !== undefined && it.verdict_text !== it.text) cl.undecided++;
    else if (it.human_verdict === "met") cl.met++;
    else if (it.human_verdict === "not_met") cl.not_met++;
    else if (it.human_verdict === "unclear") cl.unclear++;
    else cl.undecided++;
  }
  const ai = t.review.ai;
  const aiPending = (ai?.step_assessments.filter((a) => !a.decision && a.verdict !== "explains").length ?? 0)
    + (ai?.checklist_drafts.filter((d) => !d.decision).length ?? 0);
  return {
    steps,
    narrated_steps: narrated,
    pct_narrated: steps ? narrated / steps : null,
    carried_steps: carried,
    reviewer_reasoning: reviewer,
    narration_words: nw,
    avg_words_per_step: steps ? nw / steps : null,
    open_flags: open,
    dismissed_flags: dismissed,
    missing_after: missingAfter,
    checklist: cl,
    ai_pending: aiPending,
  };
}
