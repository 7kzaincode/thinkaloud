// Run: npm test  (node --test with --experimental-strip-types)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fnv1a, narrationInput } from "./hash.ts";
import { computeMetrics } from "./metrics.ts";
import * as R from "./review.ts";
import type { Trajectory } from "./types.ts";

const SAMPLE = new URL("../../samples/synthetic-flight/trajectory.json", import.meta.url);
const fresh = (): Trajectory => JSON.parse(readFileSync(SAMPLE, "utf-8"));

test("hash vectors match the Python implementation", () => {
  assert.equal(fnv1a("hello"), "4f9f2cab");
  assert.equal(fnv1a(""), "811c9dc5");
  assert.equal(fnv1a("café → ••"), "f0265585");
  assert.equal(fnv1a("s000001\nClicked the Email field\nFirst I am signing in"), "1ea2a81a");
});

const aiRun = (kind: "narration" | "checklist" | "final_screen", id = "ai-1") =>
  ({ id, kind, at: "2026-09-26T00:00:00Z", model: "claude-opus-5", status: "ok" as const });

test("AI results never change reasoning, flags, verdicts or outcome", () => {
  const t = fresh();
  const s = t.steps[0];
  R.addChecklistItem(t, "Checkout shows a nonstop flight");
  const item = t.review.checklist![0];
  const before = JSON.stringify({ steps: t.steps, outcome: t.review.outcome, checklist: t.review.checklist!.map((c) => c.human_verdict) });
  R.applyAiResult(t, {
    run: aiRun("narration"),
    assessments: [{ uid: s.uid!, verdict: "does_not_explain", explanation: "x", input_hash: narrationInput(s) }],
  });
  R.applyAiResult(t, { run: aiRun("checklist", "ai-2"), drafts: [{ text: "Price is $312" }] });
  R.applyAiResult(t, { run: aiRun("final_screen", "ai-3"), checks: [{ item_id: item.id, verdict: "supported", evidence: "e", input_hash: "h" }] });
  const after = JSON.stringify({ steps: t.steps, outcome: t.review.outcome, checklist: t.review.checklist!.map((c) => c.human_verdict) });
  assert.equal(after, before);
  assert.equal(t.review.checklist!.length, 1, "drafts are not checklist items until accepted");
  assert.equal(t.review.ai!.checklist_drafts[0].decision, null);
  assert.equal(t.review.ai!.step_assessments[0].decision, null);
});

test("accepting an assessment adds a reviewer flag with provenance; undo removes it", () => {
  const t = fresh();
  const s = t.steps[0];
  R.applyAiResult(t, { run: aiRun("narration"), assessments: [{ uid: s.uid!, verdict: "filler", explanation: "just 'okay'", input_hash: narrationInput(s) }] });
  R.decideAssessment(t, s.uid!, "accepted");
  const f = t.steps[0].flags.find((x) => x.provenance);
  assert.equal(f?.code, "filler_narration");
  assert.equal(f?.source, "reviewer");
  R.decideAssessment(t, s.uid!, null);
  assert.ok(!t.steps[0].flags.some((x) => x.provenance));
});

test("editing the reasoning makes the assessment stale and blocks acceptance", () => {
  const t = fresh();
  const s = t.steps[0];
  R.applyAiResult(t, { run: aiRun("narration"), assessments: [{ uid: s.uid!, verdict: "does_not_explain", explanation: "x", input_hash: narrationInput(s) }] });
  assert.equal(R.assessmentStale(t, t.review.ai!.step_assessments[0]), false);
  R.setReasoning(t, 0, "Clicking the email box to sign in");
  assert.equal(R.assessmentStale(t, t.review.ai!.step_assessments[0]), true);
  assert.throws(() => R.decideAssessment(t, s.uid!, "accepted"), /stale/);
});

test("final-screen check: human must accept; item edit makes it stale", () => {
  const t = fresh();
  const id = R.addChecklistItem(t, "Nonstop");
  const item = () => t.review.checklist!.find((c) => c.id === id)!;
  const { finalCheckInput } = { finalCheckInput: (x: string) => fnv1a(`${x}\n${t.success_criteria}\n${t.final_observation?.file ?? t.final_screenshot ?? ""}`) };
  R.applyAiResult(t, { run: aiRun("final_screen"), checks: [{ item_id: id, verdict: "contradicted", evidence: "1 stop", input_hash: finalCheckInput("Nonstop") }] });
  assert.equal(item().human_verdict, null);
  R.acceptAiCheck(t, id);
  assert.equal(item().human_verdict, "not_met");
  assert.equal(item().human_verdict_source, "accepted_ai_suggestion");
  R.editChecklistItem(t, id, "Nonstop flight shown");
  assert.equal(R.checkStale(t, item()), true);
  assert.throws(() => R.acceptAiCheck(t, id), /stale/);
  R.setItemVerdict(t, id, "met");
  assert.equal(item().human_verdict_source, "reviewer");
});

test("drafts become checklist items only when accepted, with edits", () => {
  const t = fresh();
  R.applyAiResult(t, { run: aiRun("checklist"), drafts: [{ text: "A" }, { text: "B" }] });
  const [a, b] = t.review.ai!.checklist_drafts;
  R.decideDraft(t, a.id, "accepted", "A (edited)");
  R.decideDraft(t, b.id, "rejected");
  assert.deepEqual(t.review.checklist!.map((c) => [c.text, c.origin, c.human_verdict]), [["A (edited)", "ai", null]]);
});

test("reasoning edits keep the original and can be reverted", () => {
  const t = fresh();
  const i = t.steps.findIndex((s) => s.flags.some((f) => f.code === "missing_reasoning"));
  R.setReasoning(t, i, "Checking the fare details");
  assert.equal(t.steps[i].reasoning_source, "reviewer");
  assert.ok(t.steps[i].dismissed_flags!.some((f) => f.code === "missing_reasoning"));
  R.revertReasoning(t, i);
  assert.equal(t.steps[i].reasoning, "");
  assert.ok(t.steps[i].flags.some((f) => f.code === "missing_reasoning"));
});

test("rebase carries matching edits and reports what no longer applies", () => {
  const reviewed = fresh();
  const i = reviewed.steps.findIndex((s) => s.flags.some((f) => f.code === "missing_reasoning"));
  R.setReasoning(reviewed, i, "Checking the fare details");
  R.addReviewerFlag(reviewed, 0, "wrong field first");
  R.setOutcome(reviewed, "pass");
  reviewed.review.base_hash = "old";
  const next = fresh();
  next.steps = next.steps.filter((_, k) => k !== 0); // step 0 disappeared after reprocessing
  const merged = R.rebaseReview(next, reviewed, "new");
  const kept = merged.steps.find((s) => s.uid === reviewed.steps[i].uid)!;
  assert.equal(kept.reasoning, "Checking the fare details");
  assert.equal(merged.review.outcome, "pass");
  assert.equal(merged.review.base_hash, "new");
  assert.equal(merged.review.rebased!.dropped.length, 1);
  assert.match(merged.review.rebased!.dropped[0], /s000001/);
});

test("metrics use documented denominators and ignore AI suggestions", () => {
  const t = fresh();
  const m = computeMetrics(t);
  const narrated = t.steps.filter((s) => s.transcript_ids.length).length;
  assert.equal(m.steps, t.steps.length);
  assert.equal(m.narrated_steps, narrated);
  assert.equal(m.pct_narrated, narrated / t.steps.length);
  const words = t.steps.flatMap((s) => s.transcript_ids).map((id) => t.transcript.find((g) => g.id === id)!.text.trim().split(/\s+/).length).reduce((a, b) => a + b, 0);
  assert.equal(m.avg_words_per_step, words / t.steps.length);
  // a reviewer-written reason is not narration
  const i = t.steps.findIndex((s) => !s.transcript_ids.length);
  R.setReasoning(t, i, "Reviewer text");
  assert.equal(computeMetrics(t).narrated_steps, narrated);
  // an AI "supported" check is not a human "met"
  const id = R.addChecklistItem(t, "x");
  R.applyAiResult(t, { run: aiRun("final_screen"), checks: [{ item_id: id, verdict: "supported", evidence: "e", input_hash: "h" }] });
  assert.equal(computeMetrics(t).checklist.met, 0);
  assert.equal(computeMetrics(t).checklist.undecided, 1);
});

test("rebase carries edits from a schema 0.1 review (no uids) by start time and action type", () => {
  const legacy = fresh();
  for (const s of legacy.steps) delete s.uid;       // how 0.1 steps looked
  R.setReasoning(legacy, 0, "Typing my email");
  R.addReviewerFlag(legacy, 3, "check this");
  R.setOutcome(legacy, "pass");
  const merged = R.rebaseReview(fresh(), legacy, "new");
  assert.equal(merged.steps[0].reasoning, "Typing my email");
  assert.ok(merged.steps[3].flags.some((f) => f.source === "reviewer" && f.detail === "check this"));
  assert.equal(merged.review.rebased!.dropped.length, 0);
  assert.equal(merged.review.outcome, "pass");
});
