// Run: npm test  (node --test with --experimental-strip-types)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { finalCheckInput, fnv1a, narrationInput } from "./hash.ts";
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

test("undo after an AI re-run removes the flag the earlier run created", () => {
  const t = fresh();
  const s = t.steps[0];
  const a = { uid: s.uid!, verdict: "filler" as const, explanation: "just 'okay'", input_hash: narrationInput(s) };
  R.applyAiResult(t, { run: aiRun("narration", "run-1"), assessments: [a] });
  R.decideAssessment(t, s.uid!, "accepted");
  R.applyAiResult(t, { run: aiRun("narration", "run-2"), assessments: [a] });
  const cur = t.review.ai!.step_assessments.find((x) => x.uid === s.uid)!;
  assert.equal(cur.run_id, "run-2");
  assert.equal(cur.decision, "accepted", "same verdict on the same input keeps the human decision");
  R.decideAssessment(t, s.uid!, null);
  assert.ok(!t.steps[0].flags.some((f) => f.provenance), "the run-1 flag is removed by undo on run-2");
  // accepting again yields exactly one provenance flag
  R.decideAssessment(t, s.uid!, "accepted");
  R.decideAssessment(t, s.uid!, "accepted");
  assert.equal(t.steps[0].flags.filter((f) => f.provenance).length, 1);
});

test("a re-run never changes an accepted verdict; only a different verdict offers to adopt it", () => {
  const t = fresh();
  const id = R.addChecklistItem(t, "Nonstop");
  const item = () => t.review.checklist!.find((c) => c.id === id)!;
  const h = finalCheckInput("Nonstop", t.success_criteria, t.final_observation?.file ?? t.final_screenshot);
  R.applyAiResult(t, { run: aiRun("final_screen", "run-1"), checks: [{ item_id: id, verdict: "supported", evidence: "e", input_hash: h }] });
  assert.equal(R.aiCheckDiffers(item()), true);
  R.acceptAiCheck(t, id);
  assert.equal(R.aiCheckDiffers(item()), false);
  R.applyAiResult(t, { run: aiRun("final_screen", "run-2"), checks: [{ item_id: id, verdict: "supported", evidence: "e2", input_hash: h }] });
  assert.equal(R.aiCheckDiffers(item()), false, "same verdict again: nothing to adopt");
  R.applyAiResult(t, { run: aiRun("final_screen", "run-3"), checks: [{ item_id: id, verdict: "contradicted", evidence: "1 stop", input_hash: h }] });
  assert.equal(item().human_verdict, "met", "the human verdict is not overwritten by a later run");
  assert.equal(item().accepted_from!.run_id, "run-1");
  assert.equal(R.aiCheckDiffers(item()), true);
  // rewording the item after deciding flags the verdict as given for other wording
  R.editChecklistItem(t, id, "Nonstop and under $400");
  assert.equal(R.verdictOutdated(item()), true);
  R.setItemVerdict(t, id, "not_met");
  assert.equal(R.verdictOutdated(item()), false);
  assert.equal(item().accepted_from, undefined);
});

test("clearing the reasoning is a reviewer edit that can be reverted", () => {
  const t = fresh();
  const i = t.steps.findIndex((s) => s.reasoning_source === "narrated");
  const said = t.steps[i].reasoning;
  R.setReasoning(t, i, "");
  assert.equal(t.steps[i].reasoning, "");
  assert.equal(t.steps[i].reasoning_source, null);
  assert.equal(t.steps[i].reasoning_original!.text, said);
  R.revertReasoning(t, i);
  assert.equal(t.steps[i].reasoning, said);
  assert.equal(t.steps[i].reasoning_source, "narrated");
  // on a step that had no reasoning: write one, then clear it -> missing_reasoning is back
  const j = t.steps.findIndex((s) => s.flags.some((f) => f.code === "missing_reasoning"));
  R.setReasoning(t, j, "why");
  assert.ok(!t.steps[j].flags.some((f) => f.code === "missing_reasoning"));
  R.setReasoning(t, j, "  ");
  assert.equal(t.steps[j].flags.filter((f) => f.code === "missing_reasoning").length, 1);
  assert.ok(!t.steps[j].dismissed_flags!.some((f) => f.code === "missing_reasoning"));
});

test("rebase keeps a dismissal when only the flag's detail text changed", () => {
  const reviewed = fresh();
  const i = reviewed.steps.findIndex((s) => s.flags.some((f) => f.code === "idle_gap"));
  R.dismissFlag(reviewed, i, reviewed.steps[i].flags.findIndex((f) => f.code === "idle_gap"));
  const next = fresh();
  next.steps[i].flags.find((f) => f.code === "idle_gap")!.detail = "24.1s with no actions before this step";
  const merged = R.rebaseReview(next, reviewed, "new");
  assert.ok(!merged.steps[i].flags.some((f) => f.code === "idle_gap"));
  assert.equal(merged.steps[i].dismissed_flags!.find((f) => f.code === "idle_gap")!.detail, "24.1s with no actions before this step");
  assert.deepEqual(merged.review.rebased!.dropped, []);
});

test("rebase carries a cleared reasoning and a save-time rebase is not recorded", () => {
  const reviewed = fresh();
  const i = reviewed.steps.findIndex((s) => s.reasoning_source === "narrated");
  R.setReasoning(reviewed, i, "");
  const merged = R.rebaseReview(fresh(), reviewed, "new");
  assert.equal(merged.steps[i].reasoning, "");
  assert.equal(merged.steps[i].reasoning_source, null);
  assert.equal(merged.steps[i].reasoning_original!.text, fresh().steps[i].reasoning);
  const quiet = R.rebaseReview(fresh(), reviewed, "same", { record: false });
  assert.equal(quiet.review.rebased, undefined);
  assert.equal(quiet.review.base_hash, "same");
});

test("the save payload carries every review edit through the server-side merge, and stays small", () => {
  const t = fresh();
  const i = t.steps.findIndex((s) => s.flags.some((f) => f.code === "missing_reasoning"));
  R.setReasoning(t, i, "Checking the fare details");
  R.addReviewerFlag(t, 0, "wrong field first");
  R.dismissFlag(t, 1, 0);
  R.setOutcome(t, "pass");
  const payload = R.reviewPayload(t, "2026-09-26T00:00:00Z");
  const merged = R.rebaseReview(fresh(), JSON.parse(JSON.stringify(payload)), "h", { record: false });
  for (const k of [0, 1, i]) {
    assert.deepEqual([merged.steps[k].reasoning, merged.steps[k].flags, merged.steps[k].dismissed_flags ?? []],
      [t.steps[k].reasoning, t.steps[k].flags, t.steps[k].dismissed_flags ?? []]);
  }
  assert.equal(merged.review.outcome, "pass");
  assert.ok(JSON.stringify(payload).length < JSON.stringify(t).length / 2);
  // a long recording still fits under the 64 KB keepalive cap
  const big = fresh();
  while (big.steps.length < 120) big.steps.push(...structuredClone(fresh().steps).map((s, k) => ({ ...s, uid: `x${big.steps.length + k}` })));
  assert.ok(JSON.stringify(R.reviewPayload(big, "t")).length < 60_000);
});

test("a dismissed privacy flag only stays dismissed for the same content after reprocessing", () => {
  const reviewed = fresh();
  const i = reviewed.steps.findIndex((s) => s.flags.some((f) => f.code === "possible_email"));
  const k = reviewed.steps[i].flags.findIndex((f) => f.code === "possible_email");
  reviewed.steps[i].flags[k].subject = "aaaa";
  R.dismissFlag(reviewed, i, k);
  const same = fresh();
  same.steps[i].flags.find((f) => f.code === "possible_email")!.subject = "aaaa";
  assert.ok(!R.rebaseReview(same, reviewed, "h").steps[i].flags.some((f) => f.code === "possible_email"));
  const other = fresh();
  other.steps[i].flags.find((f) => f.code === "possible_email")!.subject = "bbbb";
  const merged = R.rebaseReview(other, reviewed, "h");
  assert.ok(merged.steps[i].flags.some((f) => f.code === "possible_email"), "different content: reopened");
  assert.ok(merged.review.rebased!.dropped.some((d) => d.includes("different content")));
});

test("a verdict given for earlier wording counts as undecided until confirmed", () => {
  const t = fresh();
  const id = R.addChecklistItem(t, "Nonstop");
  R.setItemVerdict(t, id, "met");
  assert.equal(computeMetrics(t).checklist.met, 1);
  R.editChecklistItem(t, id, "Nonstop and under $400");
  assert.deepEqual([computeMetrics(t).checklist.met, computeMetrics(t).checklist.undecided], [0, 1]);
  R.confirmVerdict(t, id);
  assert.equal(computeMetrics(t).checklist.met, 1);
  assert.equal(R.verdictOutdated(t.review.checklist![0]), false);
});
