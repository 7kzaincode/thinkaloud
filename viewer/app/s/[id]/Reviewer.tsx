"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Flag, Step, Trajectory } from "@/lib/types";
import { describe, fmtTime } from "@/lib/format";
import Timeline from "./Timeline";

type Sel = number | "end";
type SaveState = { kind: "clean" | "dirty" | "saving" | "saved" | "error"; at?: string };

export default function Reviewer({ id, initial, hadReview }: { id: string; initial: Trajectory; hadReview: boolean }) {
  const [t, setT] = useState<Trajectory>(initial);
  const [sel, setSel] = useState<Sel>(() => {
    const i = initial.steps.findIndex((s) => s.flags.length);
    return i >= 0 ? i : 0;
  });
  const [save, setSave] = useState<SaveState>({ kind: hadReview ? "saved" : "clean" });
  const [newFlag, setNewFlag] = useState("");
  const touched = useRef(false); // only autosave after a real edit

  const step: Step | null = typeof sel === "number" ? t.steps[sel] ?? null : null;

  // ---- edits -------------------------------------------------------------
  const edit = useCallback((fn: (d: Trajectory) => void) => {
    touched.current = true;
    setT((prev) => {
      const d: Trajectory = structuredClone(prev);
      fn(d);
      d.review.edited = true;
      return d;
    });
  }, []);

  const editStep = (i: number, fn: (s: Step) => void) =>
    edit((d) => {
      fn(d.steps[i]);
      d.steps[i].edited = true;
    });

  const setReasoning = (i: number, text: string) =>
    editStep(i, (s) => {
      s.reasoning = text;
      s.reasoning_source = text.trim() ? "reviewer" : null;
      s.carried_from = null;
      // A reviewer-written reason resolves "missing_reasoning"; keep it on record.
      if (text.trim()) {
        const resolved = s.flags.filter((f) => f.code === "missing_reasoning");
        s.flags = s.flags.filter((f) => f.code !== "missing_reasoning");
        s.dismissed_flags = [...(s.dismissed_flags ?? []), ...resolved];
      }
    });

  const dismiss = (i: number, k: number) =>
    editStep(i, (s) => {
      const [f] = s.flags.splice(k, 1);
      s.dismissed_flags = [...(s.dismissed_flags ?? []), f];
    });

  const restore = (i: number, k: number) =>
    editStep(i, (s) => {
      const [f] = (s.dismissed_flags ?? []).splice(k, 1);
      s.flags.push(f);
    });

  const addFlag = (i: number) => {
    const note = newFlag.trim();
    editStep(i, (s) => {
      s.flags.push({ code: "reviewer_flag", severity: "warn", detail: note || "Flagged by reviewer.", source: "reviewer" });
    });
    setNewFlag("");
  };

  // ---- persistence: autosave to trajectory.reviewed.json -----------------------
  useEffect(() => {
    if (!touched.current) return;
    setSave({ kind: "dirty" });
    const h = setTimeout(async () => {
      setSave({ kind: "saving" });
      const body: Trajectory = { ...t, review: { ...t.review, reviewed_at: new Date().toISOString() } };
      const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).catch(() => null);
      setSave(r?.ok ? { kind: "saved", at: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) } : { kind: "error" });
    }, 700);
    return () => clearTimeout(h);
  }, [t, id]);

  const exportJson = () => {
    const out: Trajectory = { ...t, review: { ...t.review, reviewed_at: new Date().toISOString() } };
    const url = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `${t.session_id}.trajectory.json` });
    a.click();
    URL.revokeObjectURL(url);
  };

  const resetReview = async () => {
    if (!confirm("Discard all review edits and reload the processor output?")) return;
    await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    location.reload();
  };

  // ---- navigation ---------------------------------------------------------------
  const flaggedIdx = useMemo(() => t.steps.map((s, i) => (s.flags.length ? i : -1)).filter((i) => i >= 0), [t]);
  const go = useCallback(
    (d: 1 | -1) =>
      setSel((cur) => {
        const n = t.steps.length;
        if (cur === "end") return d < 0 ? n - 1 : "end";
        const nx = cur + d;
        return nx >= n ? "end" : Math.max(0, nx);
      }),
    [t.steps.length],
  );
  const nextFlagged = useCallback(() => {
    const cur = sel === "end" ? -1 : sel;
    const nx = flaggedIdx.find((i) => i > cur) ?? flaggedIdx[0];
    if (nx !== undefined) setSel(nx);
  }, [flaggedIdx, sel]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "TEXTAREA" || tag === "INPUT" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight" || e.key === "j") go(1);
      else if (e.key === "ArrowLeft" || e.key === "k") go(-1);
      else if (e.key === "n") nextFlagged();
      else if (e.key === "e") setSel("end");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, nextFlagged]);

  // ---- derived ----------------------------------------------------------------------
  const open = t.steps.flatMap((s) => s.flags);
  const openBy = (sev: string) => open.filter((f) => f.severity === sev).length;
  const shot = sel === "end" ? t.final_screenshot : step?.screenshot;
  const W = t.screen.w || 1, H = t.screen.h || 1;
  const marker = step && (step.action.type === "click" || step.action.type === "scroll") ? step.action : null;

  return (
    <div className="rv">
      <header className="rv-head">
        <div>
          <div className="eyebrow">
            <Link href="/">← sessions</Link> · {t.session_id}
            {t.duration_s != null && <> · {fmtTime(t.duration_s)}</>}
          </div>
          <h1>{t.task || "Untitled task"}</h1>
          <div className="criteria">
            Done when:{" "}
            {t.success_criteria ? <b>{t.success_criteria}</b> : <span className="missing">no success criteria recorded</span>}
          </div>
        </div>
        <div className="rv-actions">
          <span className={`save-state ${save.kind === "dirty" || save.kind === "error" ? "dirty" : ""}`} aria-live="polite">
            {save.kind === "clean" && "No edits yet"}
            {save.kind === "dirty" && "Unsaved changes"}
            {save.kind === "saving" && "Saving…"}
            {save.kind === "saved" && `Review saved${save.at ? " " + save.at : ""}`}
            {save.kind === "error" && "Save failed"}
          </span>
          <div className="seg" role="group" aria-label="Outcome">
            {([["pass", "Task done"], ["fail", "Not done"], [null, "Undecided"]] as const).map(([v, label]) => (
              <button key={String(v)} className={v ?? "none"} aria-pressed={t.review.outcome === v}
                onClick={() => edit((d) => { d.review.outcome = v; })}>
                {label}
              </button>
            ))}
          </div>
          <button className="btn primary" onClick={exportJson}>Export JSON</button>
          <button className="btn ghost" onClick={resetReview} title="Discard review edits">Reset</button>
        </div>
        <div className="qc-line">
          <span className="summary">{t.qc.summary}</span>
          {openBy("high") > 0 && <span className="pill high"><span className="sev high" />{openBy("high")} privacy open</span>}
          {openBy("warn") > 0 && <span className="pill"><span className="sev warn" />{openBy("warn")} to check</span>}
          {t.session_flags.map((f) => (
            <span key={f.code} className="pill" title={f.detail}><span className={`sev ${f.severity}`} />{f.code.replaceAll("_", " ")}</span>
          ))}
          {t.qc.redacted_steps ? <span className="pill">{t.qc.redacted_steps} typed values redacted</span> : null}
        </div>
      </header>

      <Timeline t={t} sel={sel} onSelect={setSel} />

      <div className="rv-body">
        <section className="stage">
          <div className="shot-wrap">
            <div className="shot">
              {shot ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={`/api/sessions/${encodeURIComponent(id)}/frame/${shot}`} alt={sel === "end" ? "Final screen" : `Screen before step ${sel}`} />
              ) : (
                <div className="noshot">No screenshot for this step.</div>
              )}
              {shot && marker && (
                <div className={`marker ${marker.type === "scroll" ? "scroll" : ""}`}
                  style={{ left: `${(marker.x / W) * 100}%`, top: `${(marker.y / H) * 100}%` }} />
              )}
            </div>
          </div>
          <div className="shot-caption mono">
            <span>{sel === "end" ? "final screen" : shot ?? "—"}</span>
            <span>{sel === "end" ? "compare against “done when”" : "screen as the expert saw it before acting"}</span>
          </div>
        </section>

        <aside className="side">
          <div className="step-nav">
            <div className="step-no">
              {sel === "end" ? <b>End state</b> : (
                <><b>#{String(sel).padStart(2, "0")}</b> <span style={{ color: "var(--muted)" }}>/ {t.steps.length}</span></>
              )}
              {step && <div style={{ color: "var(--muted)" }}>{fmtTime(step.t_start)} → {fmtTime(step.t_end)}</div>}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn" onClick={() => go(-1)} disabled={sel === 0} aria-label="Previous step">←</button>
              <button className="btn" onClick={() => go(1)} disabled={sel === "end"} aria-label="Next step">→</button>
              <button className="btn" onClick={nextFlagged} disabled={!flaggedIdx.length}>Next flag</button>
            </div>
          </div>

          {step ? (
            <>
              <div>
                <div className="eyebrow" style={{ marginBottom: 8 }}>What they did</div>
                <ActionLine step={step} />
              </div>

              <div>
                <div className="why-head">
                  <span className="eyebrow">Why</span>
                  <span className="src">
                    {step.reasoning_source === "narrated" && "from narration"}
                    {step.reasoning_source === "reviewer" && "edited by reviewer"}
                    {step.reasoning_source === "carried" && step.carried_from !== null && (
                      <>carried from <button onClick={() => setSel(step.carried_from!)}>#{step.carried_from}</button></>
                    )}
                    {!step.reasoning_source && "none"}
                  </span>
                </div>
                <textarea
                  className={`why ${step.reasoning_source === "carried" ? "carried" : ""}`}
                  value={step.reasoning}
                  placeholder="No reasoning captured. Write what the expert was thinking here."
                  onChange={(e) => setReasoning(sel as number, e.target.value)}
                  aria-label="Reasoning for this step"
                />
              </div>

              <div>
                <div className="eyebrow" style={{ marginBottom: 8 }}>Flags</div>
                <div className="flags">
                  {step.flags.length === 0 && !(step.dismissed_flags?.length) && <div className="none-yet">Nothing flagged on this step.</div>}
                  {step.flags.map((f, k) => (
                    <FlagRow key={`o${k}`} f={f} onAction={() => dismiss(sel as number, k)} actionLabel="Dismiss" />
                  ))}
                  {step.dismissed_flags?.map((f, k) => (
                    <FlagRow key={`d${k}`} f={f} dismissed onAction={() => restore(sel as number, k)} actionLabel="Restore" />
                  ))}
                  <form className="add-flag" onSubmit={(e) => { e.preventDefault(); addFlag(sel as number); }}>
                    <input value={newFlag} onChange={(e) => setNewFlag(e.target.value)} placeholder="Why is this step a problem?" aria-label="New flag note" />
                    <button className="btn" type="submit">Flag</button>
                  </form>
                </div>
              </div>
            </>
          ) : (
            <>
              <div>
                <div className="eyebrow" style={{ marginBottom: 8 }}>Verify the outcome</div>
                <p style={{ margin: 0 }}>
                  Does the final screen satisfy <b>{t.success_criteria || "the task"}</b>? Pick an outcome at the top. Only
                  trajectories marked <em>Task done</em> should be used as positive demonstrations.
                </p>
              </div>
              <div className="notes">
                <div className="eyebrow" style={{ marginBottom: 8 }}>Reviewer notes</div>
                <textarea value={t.review.notes} onChange={(e) => edit((d) => { d.review.notes = e.target.value; })}
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
          )}

          <div className="keys">
            <kbd>←</kbd> <kbd>→</kbd> step · <kbd>n</kbd> next flag · <kbd>e</kbd> end state
          </div>
        </aside>
      </div>
    </div>
  );
}

function ActionLine({ step }: { step: Step }) {
  const { verb, body } = describe(step.action);
  return (
    <div className="action">
      <span className="verb">{verb}</span>
      {step.action.type === "type" && step.action.redacted ? <span className="redacted">redacted</span> : body}
    </div>
  );
}

function FlagRow({ f, dismissed, onAction, actionLabel }: { f: Flag; dismissed?: boolean; onAction: () => void; actionLabel: string }) {
  return (
    <div className={`flag ${dismissed ? "dismissed" : f.severity}`}>
      <span className={`sev ${f.severity}`} />
      <span className="code">{f.code.replaceAll("_", " ")} {f.source === "reviewer" && <span className="by">· reviewer</span>}</span>
      <button className="btn ghost" style={{ padding: "0 4px" }} onClick={onAction}>{actionLabel}</button>
      <span className="detail">{f.detail}</span>
    </div>
  );
}
