/**
 * Review state transitions. Pure functions over a trajectory draft (callers clone).
 *
 * Rules enforced here (and tested in review.test.ts):
 *  - AI results only ever add suggestions. They never change reasoning, flags,
 *    checklist verdicts or the outcome. A human action (decide*, accept*, set*)
 *    is required for any of those.
 *  - Every AI evaluation carries the hash of the input it judged. If the reviewer
 *    later edits that input (reasoning, checklist item text), the evaluation is
 *    reported stale and can no longer be accepted.
 *  - The processor's original reasoning is kept when a reviewer edits it.
 */
import { finalCheckInput, narrationInput } from "./hash.ts";
import type { AiRun, ChecklistItem, Flag, Step, StepAssessment, Trajectory } from "./types.ts";

export type Draft = Trajectory;

export interface AiResult {
  run: AiRun;
  assessments?: { uid: string; verdict: StepAssessment["verdict"]; explanation: string; input_hash: string }[];
  drafts?: { text: string }[];
  checks?: { item_id: string; verdict: "supported" | "contradicted" | "unknown"; evidence: string; input_hash: string }[];
}

const nowIso = () => new Date().toISOString();

function ai(d: Draft) {
  d.review.ai ??= { runs: [], step_assessments: [], checklist_drafts: [] };
  return d.review.ai;
}

function touch(d: Draft, step?: Step) {
  d.review.edited = true;
  if (step) step.edited = true;
}

export function stepKey(s: Step): string {
  return s.uid ?? timeKey(s);
}

/** Identity used by schema 0.1 steps (no uid): start time and action type. */
export function timeKey(s: Step): string {
  return `t${s.t_start}|${s.action.type}`;
}

// ---- step edits ---------------------------------------------------------------
export function setReasoning(d: Draft, i: number, text: string): void {
  const s = d.steps[i];
  if (!s.reasoning_original) {
    s.reasoning_original = { text: s.reasoning, source: s.reasoning_source, carried_from: s.carried_from };
  }
  s.reasoning = text;
  s.reasoning_source = text.trim() ? "reviewer" : null;
  s.carried_from = null;
  if (text.trim()) {
    // a reviewer-written reason resolves "missing_reasoning"; keep it on record
    const resolved = s.flags.filter((f) => f.code === "missing_reasoning");
    s.flags = s.flags.filter((f) => f.code !== "missing_reasoning");
    s.dismissed_flags = [...(s.dismissed_flags ?? []), ...resolved];
  } else {
    // cleared again: the step has no reasoning, so the flag applies again
    const back = (s.dismissed_flags ?? []).filter((f) => f.code === "missing_reasoning");
    s.dismissed_flags = (s.dismissed_flags ?? []).filter((f) => f.code !== "missing_reasoning");
    s.flags.push(...back);
  }
  touch(d, s);
}

export function revertReasoning(d: Draft, i: number): void {
  const s = d.steps[i];
  if (!s.reasoning_original) return;
  s.reasoning = s.reasoning_original.text;
  s.reasoning_source = s.reasoning_original.source;
  s.carried_from = s.reasoning_original.carried_from;
  delete s.reasoning_original;
  const back = (s.dismissed_flags ?? []).filter((f) => f.code === "missing_reasoning");
  if (!s.reasoning.trim() && back.length) {
    s.dismissed_flags = (s.dismissed_flags ?? []).filter((f) => f.code !== "missing_reasoning");
    s.flags.push(...back);
  }
  touch(d, s);
}

export function dismissFlag(d: Draft, i: number, k: number): void {
  const s = d.steps[i];
  const [f] = s.flags.splice(k, 1);
  if (f) s.dismissed_flags = [...(s.dismissed_flags ?? []), f];
  touch(d, s);
}

export function restoreFlag(d: Draft, i: number, k: number): void {
  const s = d.steps[i];
  const [f] = (s.dismissed_flags ?? []).splice(k, 1);
  if (f) s.flags.push(f);
  touch(d, s);
}

export function addReviewerFlag(d: Draft, i: number, note: string): void {
  const s = d.steps[i];
  s.flags.push({ code: "reviewer_flag", severity: "warn", detail: note.trim() || "Flagged by reviewer.", source: "reviewer" });
  touch(d, s);
}

export function setOutcome(d: Draft, v: "pass" | "fail" | null): void {
  d.review.outcome = v;
  touch(d);
}

export function setNotes(d: Draft, notes: string): void {
  d.review.notes = notes;
  touch(d);
}

// ---- AI results: suggestions only ------------------------------------------------
export function applyAiResult(d: Draft, r: AiResult): void {
  const st = ai(d);
  st.runs.push(r.run);
  if (r.run.status !== "ok") return;
  for (const a of r.assessments ?? []) {
    const prev = st.step_assessments.find((x) => x.uid === a.uid);
    const carried = prev && prev.decision && prev.verdict === a.verdict && prev.input_hash === a.input_hash;
    const next: StepAssessment = {
      uid: a.uid, run_id: r.run.id, verdict: a.verdict, explanation: a.explanation, input_hash: a.input_hash,
      decision: carried ? prev!.decision : null, decided_at: carried ? prev!.decided_at : undefined,
    };
    st.step_assessments = st.step_assessments.filter((x) => x.uid !== a.uid).concat(next);
  }
  for (const [n, dr] of (r.drafts ?? []).entries()) {
    st.checklist_drafts.push({ id: `${r.run.id}-${n}`, text: dr.text, run_id: r.run.id, decision: null });
  }
  for (const c of r.checks ?? []) {
    const item = (d.review.checklist ?? []).find((x) => x.id === c.item_id);
    if (item) item.ai_check = { run_id: r.run.id, verdict: c.verdict, evidence: c.evidence, input_hash: c.input_hash };
  }
  // not a human edit: review.edited stays as it was
}

// ---- staleness --------------------------------------------------------------------
export function assessmentStale(d: Draft, a: StepAssessment): boolean {
  const s = d.steps.find((x) => stepKey(x) === a.uid);
  return !s || narrationInput(s) !== a.input_hash;
}

export function checkStale(d: Draft, item: ChecklistItem): boolean {
  if (!item.ai_check) return false;
  const final = d.final_observation?.file ?? d.final_screenshot;
  return finalCheckInput(item.text, d.success_criteria, final) !== item.ai_check.input_hash;
}

// ---- human decisions on AI suggestions ------------------------------------------------
const FLAG_FOR: Record<string, { code: string; text: string } | undefined> = {
  does_not_explain: { code: "narration_mismatch", text: "Narration does not explain this action" },
  partially_explains: { code: "partial_explanation", text: "Narration only partly explains this action" },
  filler: { code: "filler_narration", text: "Narration is filler" },
  missing: { code: "missing_explanation", text: "No explanation for this action" },
};

/** Accepting a critical assessment turns it into a reviewer flag (with provenance). */
export function decideAssessment(d: Draft, uid: string, decision: "accepted" | "rejected" | null): void {
  const st = ai(d);
  const a = st.step_assessments.find((x) => x.uid === uid);
  const s = d.steps.find((x) => stepKey(x) === uid);
  if (!a || !s) return;
  if (decision === "accepted" && assessmentStale(d, a)) throw new Error("stale suggestion: re-run the check first");
  const isProv = (f: Flag) => f.provenance?.suggestion === `assessment:${uid}`;
  s.flags = s.flags.filter((f) => !isProv(f));
  a.decision = decision;
  a.decided_at = decision ? nowIso() : undefined;
  const fl = FLAG_FOR[a.verdict];
  if (decision === "accepted" && fl) {
    s.flags.push({ code: fl.code, severity: "warn", detail: `${fl.text}: ${a.explanation}`, source: "reviewer",
      provenance: { ai_run_id: a.run_id, suggestion: `assessment:${uid}` } });
  }
  touch(d, s);
}

let counter = 0;
const newId = () => `c${Date.now().toString(36)}${(counter++).toString(36)}`;

export function decideDraft(d: Draft, draftId: string, decision: "accepted" | "rejected", editedText?: string): void {
  const st = ai(d);
  const dr = st.checklist_drafts.find((x) => x.id === draftId);
  if (!dr || dr.decision) return;
  dr.decision = decision;
  if (decision === "accepted") {
    d.review.checklist = [...(d.review.checklist ?? []), {
      id: newId(), text: (editedText ?? dr.text).trim(), origin: "ai", ai_run_id: dr.run_id, human_verdict: null, ai_check: null,
    }];
  }
  touch(d);
}

export function addChecklistItem(d: Draft, text: string): string {
  const id = newId();
  d.review.checklist = [...(d.review.checklist ?? []), { id, text: text.trim(), origin: "human", human_verdict: null, ai_check: null }];
  touch(d);
  return id;
}

export function editChecklistItem(d: Draft, id: string, text: string): void {
  const item = (d.review.checklist ?? []).find((x) => x.id === id);
  if (!item) return;
  item.text = text;
  touch(d); // any ai_check is now stale by hash
}

export function removeChecklistItem(d: Draft, id: string): void {
  d.review.checklist = (d.review.checklist ?? []).filter((x) => x.id !== id);
  touch(d);
}

export function setItemVerdict(d: Draft, id: string, verdict: ChecklistItem["human_verdict"]): void {
  const item = (d.review.checklist ?? []).find((x) => x.id === id);
  if (!item) return;
  item.human_verdict = verdict;
  item.human_verdict_source = verdict ? "reviewer" : undefined;
  item.verdict_text = verdict ? item.text : undefined;
  delete item.accepted_from;
  touch(d);
}

/** The verdict was given for different wording than the item has now. */
export function verdictOutdated(item: ChecklistItem): boolean {
  return !!item.human_verdict && item.verdict_text !== undefined && item.verdict_text !== item.text;
}

/** The latest AI check would give a different verdict than the one the human holds now. */
export function aiCheckDiffers(item: ChecklistItem): boolean {
  return !!item.ai_check && VERDICT_MAP[item.ai_check.verdict] !== item.human_verdict;
}

const VERDICT_MAP = { supported: "met", contradicted: "not_met", unknown: "unclear" } as const;

export function acceptAiCheck(d: Draft, id: string): void {
  const item = (d.review.checklist ?? []).find((x) => x.id === id);
  if (!item?.ai_check) return;
  if (checkStale(d, item)) throw new Error("stale suggestion: re-run the check first");
  item.human_verdict = VERDICT_MAP[item.ai_check.verdict];
  item.human_verdict_source = "accepted_ai_suggestion";
  item.accepted_from = { run_id: item.ai_check.run_id, verdict: item.ai_check.verdict, input_hash: item.ai_check.input_hash };
  item.verdict_text = item.text;
  touch(d);
}

/** Same rule as the exporter (export.py dismissal_applies). */
export function dismissalApplies(dismissed: Flag, fresh: Flag): boolean {
  if (dismissed.code !== fresh.code) return false;
  return fresh.severity !== "high" || dismissed.subject === fresh.subject;
}

/** The verdict still holds for the reworded item: record that it was checked against the new wording. */
export function confirmVerdict(d: Draft, id: string): void {
  const item = (d.review.checklist ?? []).find((x) => x.id === id);
  if (!item?.human_verdict) return;
  item.verdict_text = item.text;
  touch(d);
}

/**
 * What the review page sends when saving: only review-owned fields. The server lays them over
 * the processor's trajectory.json (rebaseReview) and ignores everything else, so sending the
 * whole trajectory would only make the request big (browsers cap keepalive requests at 64 KB).
 */
export function reviewPayload(t: Trajectory, reviewedAt: string): Trajectory {
  return {
    session_id: t.session_id,
    review: { ...t.review, reviewed_at: reviewedAt },
    steps: t.steps.map((s) => ({
      uid: s.uid, t_start: s.t_start, action: { type: s.action.type },
      reasoning: s.reasoning, reasoning_source: s.reasoning_source, carried_from: s.carried_from,
      reasoning_original: s.reasoning_original, flags: s.flags, dismissed_flags: s.dismissed_flags, edited: s.edited,
    })),
  } as unknown as Trajectory;
}

// ---- rebasing a review onto a reprocessed trajectory -------------------------------------
/**
 * The processor's output changed (reprocessed, new rules). Keep every human decision
 * that still applies: per-step edits are carried to the step with the same key and the
 * same action; everything that no longer matches is listed in review.rebased.dropped.
 */
export function rebaseReview(fresh: Trajectory, reviewed: Trajectory, freshHash: string,
                             opts: { record?: boolean } = { record: true }): Trajectory {
  const out: Trajectory = structuredClone(fresh);
  const old = new Map(reviewed.steps.map((s) => [stepKey(s), s]));
  // reviews made on schema 0.1 steps have no uids: match those by start time + action type
  const oldByTime = new Map(reviewed.steps.filter((s) => !s.uid).map((s) => [timeKey(s), s]));
  const dropped: string[] = [];
  const kept = new Set<Step>();
  for (const s of out.steps) {
    const o = old.get(stepKey(s)) ?? oldByTime.get(timeKey(s));
    if (!o || kept.has(o)) continue;
    if (o.action.type !== s.action.type) continue;
    kept.add(o);
    if (o.reasoning_source === "reviewer" || o.reasoning_original) {
      // a reviewer wrote, edited or cleared this reasoning: their version wins
      s.reasoning_original = { text: s.reasoning, source: s.reasoning_source, carried_from: s.carried_from };
      s.reasoning = typeof o.reasoning === "string" ? o.reasoning.slice(0, 5000) : "";
      s.reasoning_source = s.reasoning.trim() ? "reviewer" : null;
      s.carried_from = null;
    }
    // dismissals match by flag code (detail text can change between processing runs, e.g. seconds);
    // privacy flags also by the content they are about
    const qcDismissed = (o.dismissed_flags ?? []).filter((f) => f?.source !== "reviewer" && typeof f?.code === "string");
    const closed = (f: Flag) => qcDismissed.some((d) => dismissalApplies(d, f));
    s.dismissed_flags = s.flags.filter(closed);
    s.flags = s.flags.filter((f) => !closed(f));
    if (s.reasoning_source === "reviewer") {
      const mr = s.flags.filter((f) => f.code === "missing_reasoning");
      s.flags = s.flags.filter((f) => f.code !== "missing_reasoning");
      s.dismissed_flags.push(...mr);
    }
    const reviewerFlags = (o.flags ?? []).filter((f) => f?.source === "reviewer" && typeof f.code === "string")
      .map((f) => ({ code: String(f.code).slice(0, 64), severity: (["high", "warn", "info"].includes(f.severity) ? f.severity : "warn") as Flag["severity"],
        detail: String(f.detail ?? "").slice(0, 1000), source: "reviewer" as const, ...(f.provenance ? { provenance: f.provenance } : {}) }));
    s.flags.push(...reviewerFlags);
    s.dismissed_flags.push(...(o.dismissed_flags ?? []).filter((f) => f?.source === "reviewer"));
    s.edited = !!o.edited;
  }
  for (const o of reviewed.steps) {
    const touched = o.edited || o.reasoning_source === "reviewer" || o.reasoning_original || (o.dismissed_flags ?? []).length
      || (o.flags ?? []).some((f) => f?.source === "reviewer");
    if (touched && !kept.has(o)) dropped.push(`step ${stepKey(o)}: edits no longer match a step`);
    if (kept.has(o)) {
      const now = out.steps.find((s) => stepKey(s) === stepKey(o) || (!o.uid && timeKey(s) === timeKey(o)));
      for (const f of o.dismissed_flags ?? []) {
        if (f?.source !== "reviewer" && now && !now.dismissed_flags?.some((x) => dismissalApplies(f, x))) {
          dropped.push(now.flags.some((x) => x.code === f.code)
            ? `step ${stepKey(o)}: dismissed "${f.code}" is now about different content; reopened`
            : `step ${stepKey(o)}: dismissed "${f.code}" no longer raised`);
        }
      }
    }
  }
  out.review = structuredClone(reviewed.review ?? fresh.review);
  if (out.review.ai) {
    const valid = new Set(out.steps.map(stepKey));
    const gone = out.review.ai.step_assessments.filter((a) => !valid.has(a.uid));
    gone.forEach((a) => dropped.push(`AI assessment for ${a.uid}`));
    out.review.ai.step_assessments = out.review.ai.step_assessments.filter((a) => valid.has(a.uid));
  }
  if (opts.record) {
    const entry = { at: nowIso(), from_hash: reviewed.review?.base_hash ?? "unknown", dropped };
    out.review.rebased = entry;
    out.review.rebase_history = [...(out.review.rebase_history ?? []), entry].slice(-20);
  }
  out.review.base_hash = freshHash;
  return out;
}
