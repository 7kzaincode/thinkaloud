"use client";

import { useState } from "react";
import type { Flag, Step, Trajectory } from "@/lib/types";
import { describe, fmtTime, targetReliable } from "@/lib/format";
import * as R from "@/lib/review";
import type { AiStatus } from "./Reviewer";

const TIMING: Record<string, string> = {
  before_action: "said before the action",
  during_action: "said during the action",
  after_action: "said after the action (retrospective)",
};
const VERDICT: Record<string, string> = {
  explains: "Narration explains this action",
  partially_explains: "Narration only partly explains this action",
  does_not_explain: "Narration does not explain this action",
  filler: "Narration is filler",
  missing: "No explanation for this action",
};

export default function StepPanel({ t, index, step, edit, onJump, aiStatus }: {
  t: Trajectory; index: number; step: Step; edit: (fn: (d: Trajectory) => void) => void;
  onJump: (i: number) => void; aiStatus: AiStatus;
}) {
  const [newFlag, setNewFlag] = useState("");
  const key = R.stepKey(step);
  const assessment = t.review.ai?.step_assessments.find((a) => a.uid === key);
  const stale = assessment ? R.assessmentStale(t, assessment) : false;
  const segs = new Map(t.transcript.map((g) => [g.id, g]));
  const tgt = step.target;

  return (
    <>
      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>What they did</div>
        <div className="action">{describe(step)}</div>
        {step.context?.window_title && (
          <div className="context">in <b>{step.context.window_title}</b>{step.context.process ? ` · ${step.context.process}` : ""}</div>
        )}
        <details className="raw">
          <summary>Raw action and target</summary>
          <dl>
            {"x" in step.action && <><dt>frame x, y</dt><dd>{step.action.x}, {step.action.y}</dd></>}
            {"screen_x" in step.action && step.action.screen_x !== undefined && <><dt>screen x, y</dt><dd>{step.action.screen_x}, {step.action.screen_y}</dd></>}
            {step.action.type === "click" && <><dt>button</dt><dd>{step.action.button}{step.action.count ? ` ×${step.action.count}` : ""}</dd></>}
            {step.action.type === "drag" && <><dt>drag to</dt><dd>{step.action.x2}, {step.action.y2} ({step.action.button})</dd></>}
            {step.action.type === "type" && step.action.keystrokes && <><dt>keystrokes</dt><dd>{step.action.keystrokes}</dd></>}
            {tgt ? (
              <>
                <dt>target</dt><dd>{tgt.status}{tgt.status === "ok" && !targetReliable(step) ? " (lookup too late to trust)" : ""}{tgt.latency_ms != null ? ` · ${tgt.latency_ms} ms` : ""}</dd>
                {tgt.role && <><dt>role</dt><dd>{tgt.role}</dd></>}
                {tgt.name && <><dt>name</dt><dd>{tgt.name}</dd></>}
                {tgt.automation_id && <><dt>automation id</dt><dd>{tgt.automation_id}</dd></>}
                {tgt.frame_rect && <><dt>rect</dt><dd>{tgt.frame_rect.join(", ")}</dd></>}
                <dt>url</dt><dd>{tgt.url ?? `none (${tgt.url_status ?? "not looked up"})`}</dd>
                {tgt.error && <><dt>error</dt><dd>{tgt.error}</dd></>}
              </>
            ) : (step.action.type === "click" || step.action.type === "drag") && <><dt>target</dt><dd>not recorded</dd></>}
            <dt>step uid</dt><dd>{step.uid ?? "—"}</dd>
          </dl>
        </details>
      </div>

      <div>
        <div className="why-head">
          <span className="eyebrow">Why</span>
          <span className="src">
            {step.reasoning_source === "narrated" && "from narration"}
            {step.reasoning_source === "reviewer" && "edited by reviewer"}
            {step.reasoning_original && step.reasoning_source !== "reviewer" && "cleared by reviewer"}
            {step.reasoning_original && <> · <button onClick={() => edit((d) => R.revertReasoning(d, index))}>revert</button></>}
            {step.reasoning_source === "carried" && step.carried_from !== null && (
              <>carried from <button onClick={() => onJump(step.carried_from!)}>#{step.carried_from}</button></>
            )}
            {!step.reasoning_source && !step.reasoning_original && "none"}
          </span>
        </div>
        <textarea
          className={`why ${step.reasoning_source === "carried" ? "carried" : ""}`}
          value={step.reasoning}
          placeholder="No reasoning captured. Write what the expert was thinking here."
          onChange={(e) => edit((d) => R.setReasoning(d, index, e.target.value))}
          aria-label="Reasoning for this step"
        />
        {!!step.narration?.length && (
          <div className="timing">
            {step.narration.map((n) => (
              <span key={n.segment_id} className={`chip ${n.timing}`} title={segs.get(n.segment_id)?.text}>
                {fmtTime(n.t_start)} · {TIMING[n.timing]}
              </span>
            ))}
          </div>
        )}
        {step.reasoning_original && step.reasoning_original.text && (
          <div className="original">original: “{step.reasoning_original.text}”</div>
        )}
      </div>

      {assessment && (
        <div className={`ai-card ${assessment.verdict}`}>
          <div className="ai-head">
            <span className="badge">AI suggestion</span>
            {stale && <span className="badge stale" title="The reasoning or action changed after this was generated">stale</span>}
            {assessment.decision && <span className="badge">{assessment.decision}</span>}
          </div>
          <div className="ai-verdict">{VERDICT[assessment.verdict]}</div>
          <div className="ai-expl">{assessment.explanation}</div>
          {assessment.verdict !== "explains" && (
            <div className="ai-actions">
              {assessment.decision ? (
                <button className="btn ghost" onClick={() => edit((d) => R.decideAssessment(d, key, null))}>Undo</button>
              ) : (
                <>
                  <button className="btn" disabled={stale} title={stale ? "Re-run the narration check first" : "Adds a reviewer flag to this step"}
                    onClick={() => edit((d) => R.decideAssessment(d, key, "accepted"))}>Accept as flag</button>
                  <button className="btn ghost" onClick={() => edit((d) => R.decideAssessment(d, key, "rejected"))}>Dismiss</button>
                </>
              )}
            </div>
          )}
        </div>
      )}
      {!assessment && aiStatus?.configured === false && step.reasoning && (
        <div className="none-yet small">AI narration check unavailable: {aiStatus.reason}</div>
      )}

      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Flags</div>
        <div className="flags">
          {step.flags.length === 0 && !(step.dismissed_flags?.length) && <div className="none-yet">Nothing flagged on this step.</div>}
          {step.flags.map((f, k) => (
            <FlagRow key={`o${k}`} f={f} onAction={() => edit((d) => R.dismissFlag(d, index, k))} actionLabel="Dismiss" />
          ))}
          {step.dismissed_flags?.map((f, k) => (
            <FlagRow key={`d${k}`} f={f} dismissed onAction={() => edit((d) => R.restoreFlag(d, index, k))} actionLabel="Restore" />
          ))}
          <form className="add-flag" onSubmit={(e) => { e.preventDefault(); edit((d) => R.addReviewerFlag(d, index, newFlag)); setNewFlag(""); }}>
            <input value={newFlag} onChange={(e) => setNewFlag(e.target.value)} placeholder="Why is this step a problem?" aria-label="New flag note" />
            <button className="btn" type="submit">Flag</button>
          </form>
        </div>
      </div>
    </>
  );
}

function FlagRow({ f, dismissed, onAction, actionLabel }: { f: Flag; dismissed?: boolean; onAction: () => void; actionLabel: string }) {
  return (
    <div className={`flag ${dismissed ? "dismissed" : f.severity}`}>
      <span className={`sev ${f.severity}`} />
      <span className="code">
        {f.code.replaceAll("_", " ")} {f.source === "reviewer" && <span className="by">· reviewer{f.provenance ? " (from AI suggestion)" : ""}</span>}
      </span>
      <button className="btn ghost" style={{ padding: "0 4px" }} onClick={onAction}>{actionLabel}</button>
      <span className="detail">{f.detail}</span>
    </div>
  );
}
