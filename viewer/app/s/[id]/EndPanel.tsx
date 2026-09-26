"use client";

import { useState } from "react";
import type { ChecklistItem, Trajectory } from "@/lib/types";
import * as R from "@/lib/review";
import type { AiStatus } from "./Reviewer";

type Kind = "narration" | "checklist" | "final_screen";
const LABEL: Record<Kind, string> = {
  checklist: "Draft checklist from “done when”",
  final_screen: "Check final screen against checklist",
  narration: "Check narration on every step",
};
const SENDS: Record<Kind, string> = {
  checklist: "the task and “done when” text",
  final_screen: "the task, “done when”, your checklist and the final screenshot",
  narration: "the task, “done when”, each step's description and its reasoning text",
};
const CHECK_TEXT = { supported: "supported by the final screen", contradicted: "contradicted by the final screen", unknown: "can't be determined from the final screen" };

export default function EndPanel({ id, t, edit, aiStatus }: {
  id: string; t: Trajectory; edit: (fn: (d: Trajectory) => void) => void; aiStatus: AiStatus;
}) {
  const [newItem, setNewItem] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState<Kind | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const items = t.review.checklist ?? [];
  const pendingDrafts = (t.review.ai?.checklist_drafts ?? []).filter((d) => !d.decision);
  const undecided = items.filter((i) => i.human_verdict !== "met");

  const runAi = async (kind: Kind) => {
    setBusy(kind);
    setAiError(null);
    try {
      const r = await fetch(`/api/sessions/${encodeURIComponent(id)}/ai`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, trajectory: t }),
      });
      const j = await r.json();
      if (!r.ok || j.error) {
        setAiError(`${j.error_type ? j.error_type.replaceAll("_", " ") + ": " : ""}${j.error ?? `HTTP ${r.status}`}`);
        if (j.result) edit((d) => R.applyAiResult(d, j.result)); // records the failed run
      } else {
        edit((d) => R.applyAiResult(d, j.result));
      }
    } catch (e) {
      setAiError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const canRun = (k: Kind) => aiStatus?.configured && consent && !busy && (k !== "final_screen" || items.length > 0);

  return (
    <>
      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Verify the outcome</div>
        <p style={{ margin: 0 }}>
          Does the final screen satisfy <b>{t.success_criteria || "the task"}</b>? Break it into checklist items, mark each
          one yourself, then pick an outcome at the top. Only trajectories marked <em>Task done</em> should be used as
          positive demonstrations.
        </p>
        {t.review.outcome === "pass" && undecided.length > 0 && (
          <div className="alert soft">Marked “Task done” but {undecided.length} checklist item(s) aren't marked met.</div>
        )}
      </div>

      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Checklist</div>
        {items.length === 0 && <div className="none-yet">No checklist yet. Add items, or let AI draft some for you to accept.</div>}
        <div className="checklist">
          {items.map((it) => <Item key={it.id} t={t} it={it} edit={edit} />)}
        </div>
        <form className="add-flag" onSubmit={(e) => { e.preventDefault(); if (newItem.trim()) { edit((d) => { R.addChecklistItem(d, newItem); }); setNewItem(""); } }}>
          <input value={newItem} onChange={(e) => setNewItem(e.target.value)} placeholder="e.g. The flight shown is nonstop" aria-label="New checklist item" />
          <button className="btn" type="submit">Add</button>
        </form>
      </div>

      {pendingDrafts.length > 0 && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>AI-drafted items (not in the checklist until you accept)</div>
          {pendingDrafts.map((dr) => (
            <div key={dr.id} className="draft">
              <input value={drafts[dr.id] ?? dr.text} onChange={(e) => setDrafts({ ...drafts, [dr.id]: e.target.value })} aria-label="Draft item text" />
              <button className="btn" onClick={() => edit((d) => R.decideDraft(d, dr.id, "accepted", drafts[dr.id]))}>Accept</button>
              <button className="btn ghost" onClick={() => edit((d) => R.decideDraft(d, dr.id, "rejected"))}>Reject</button>
            </div>
          ))}
        </div>
      )}

      <div className="ai-box">
        <div className="eyebrow" style={{ marginBottom: 8 }}>AI assistant (suggestions only)</div>
        {aiStatus === null ? <div className="none-yet small">Checking configuration…</div> : !aiStatus.configured ? (
          <div className="none-yet small">
            Not configured: {aiStatus.reason}. Manual review works without it. To enable, add a key in{" "}
            <a href="/settings">Settings</a> (desktop app) or set <code>ANTHROPIC_API_KEY</code> or <code>GEMINI_API_KEY</code> for
            the viewer server.
          </div>
        ) : (
          <>
            <label className="consent">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>OK to send parts of this recording to {aiStatus.provider_name || "the AI provider"} ({aiStatus.model}). Nothing is sent
                until you click a button below.{aiStatus.provider === "gemini" &&
                  " Google may use content sent with free-tier (unpaid) Gemini keys to improve its products."}</span>
            </label>
            {(["checklist", "final_screen", "narration"] as Kind[]).map((k) => (
              <div key={k} className="ai-run">
                <button className="btn" disabled={!canRun(k)} onClick={() => runAi(k)}>{busy === k ? "Working…" : LABEL[k]}</button>
                <span className="small faint">sends {SENDS[k]}</span>
              </div>
            ))}
          </>
        )}
        {aiError && <div className="alert">{aiError}</div>}
        {!!t.review.ai?.runs.length && (
          <div className="small faint" style={{ marginTop: 6 }}>
            {t.review.ai.runs.length} AI run(s); last: {t.review.ai.runs.at(-1)!.kind} · {t.review.ai.runs.at(-1)!.status} ·{" "}
            {t.review.ai.runs.at(-1)!.provider ? `${t.review.ai.runs.at(-1)!.provider} ` : ""}{t.review.ai.runs.at(-1)!.model}
            {t.review.ai.runs.at(-1)!.note ? ` · ${t.review.ai.runs.at(-1)!.note}` : ""}
          </div>
        )}
      </div>

      <div className="notes">
        <div className="eyebrow" style={{ marginBottom: 8 }}>Reviewer notes</div>
        <textarea value={t.review.notes} onChange={(e) => edit((d) => R.setNotes(d, e.target.value))}
          placeholder="Anything a model trainer should know about this session." />
      </div>
      {t.transcript.some((g) => g.step_id === null) && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Narration before any action</div>
          {t.transcript.filter((g) => g.step_id === null).map((g) => (
            <p key={g.id} style={{ fontFamily: "var(--font-serif)", fontSize: 16, margin: "0 0 8px" }}>“{g.text}”</p>
          ))}
        </div>
      )}
    </>
  );
}

function Item({ t, it, edit }: { t: Trajectory; it: ChecklistItem; edit: (fn: (d: Trajectory) => void) => void }) {
  const stale = R.checkStale(t, it);
  return (
    <div className="check-item">
      <div className="check-row">
        <input className="check-text" value={it.text} onChange={(e) => edit((d) => R.editChecklistItem(d, it.id, e.target.value))} aria-label="Checklist item" />
        <button className="btn ghost" title="Remove item" onClick={() => edit((d) => R.removeChecklistItem(d, it.id))}>×</button>
      </div>
      <div className="check-row">
        <div className="seg small-seg" role="group" aria-label="Your verdict">
          {([["met", "Met"], ["not_met", "Not met"], ["unclear", "Unclear"]] as const).map(([v, label]) => (
            <button key={v} className={v === "met" ? "pass" : v === "not_met" ? "fail" : "none"} aria-pressed={it.human_verdict === v}
              onClick={() => edit((d) => R.setItemVerdict(d, it.id, it.human_verdict === v ? null : v))}>{label}</button>
          ))}
        </div>
        <span className="small faint">{it.origin === "ai" ? "drafted by AI, accepted by you" : "added by you"}{it.human_verdict_source === "accepted_ai_suggestion" ? " · verdict adopted from an AI check" : ""}</span>
      </div>
      {R.verdictOutdated(it) && (
        <div className="alert soft small">
          Your verdict was given for the earlier wording “{it.verdict_text}”; until you confirm it, it counts as undecided.{" "}
          <button className="linkish" onClick={() => edit((d) => R.confirmVerdict(d, it.id))}>It still applies</button>
        </div>
      )}
      {it.ai_check && (
        <div className={`ai-check ${it.ai_check.verdict}`}>
          <span className="badge">AI</span>{stale && <span className="badge stale">stale</span>} {CHECK_TEXT[it.ai_check.verdict]}
          <div className="ai-expl">{it.ai_check.evidence}</div>
          {!stale && R.aiCheckDiffers(it) && (
            <button className="btn ghost" onClick={() => edit((d) => R.acceptAiCheck(d, it.id))}>Use this as my verdict</button>
          )}
        </div>
      )}
    </div>
  );
}
