"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Step, Trajectory } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import * as R from "@/lib/review";
import Timeline from "./Timeline";
import Stage, { type View } from "./Stage";
import StepPanel from "./StepPanel";
import EndPanel from "./EndPanel";
import ExportDialog from "./ExportDialog";

type Sel = number | "end";
type SaveState = { kind: "clean" | "dirty" | "saving" | "saved" | "error"; at?: string; msg?: string };
export type AiStatus = { configured: boolean; model: string; reason?: string } | null;

export default function Reviewer({ id, initial, hadReview, rebased }: { id: string; initial: Trajectory; hadReview: boolean; rebased: boolean }) {
  const [t, setT] = useState<Trajectory>(initial);
  const [sel, setSel] = useState<Sel>(() => {
    const i = initial.steps.findIndex((s) => s.flags.length);
    return i >= 0 ? i : initial.steps.length ? 0 : "end";
  });
  const [view, setView] = useState<View>("before");
  const [save, setSave] = useState<SaveState>({ kind: hadReview ? "saved" : "clean" });
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [follow, setFollow] = useState(true);
  const [exportOpen, setExportOpen] = useState(false);
  const [aiStatus, setAiStatus] = useState<AiStatus>(null);
  const touched = useRef(false); // only autosave after a real edit
  const video = useRef<HTMLVideoElement>(null);

  const step: Step | null = typeof sel === "number" ? t.steps[sel] ?? null : null;

  useEffect(() => {
    fetch("/api/ai/status").then((r) => r.json()).then(setAiStatus).catch(() => setAiStatus({ configured: false, model: "", reason: "status unavailable" }));
  }, []);

  // ---- edits ---------------------------------------------------------------
  const edit = useCallback((fn: (d: Trajectory) => void) => {
    touched.current = true;
    setT((prev) => {
      const d: Trajectory = structuredClone(prev);
      fn(d);
      return d;
    });
  }, []);

  // ---- persistence: autosave to trajectory.reviewed.json -----------------------
  // One save in flight at a time (a later save can never be overwritten by an earlier one),
  // flushed before export and when leaving the page. If the recording was reprocessed while
  // the page was open, the server rebases these edits onto the new steps and we adopt that.
  const latest = useRef(t);
  latest.current = t;
  const dirty = useRef(false);
  const inflight = useRef<Promise<void> | null>(null);
  const adopting = useRef(false);

  const flush = useCallback(async (): Promise<void> => {
    while (inflight.current) await inflight.current;
    if (!dirty.current) return;
    dirty.current = false;
    const snapshot = latest.current;
    const run = (async () => {
      setSave({ kind: "saving" });
      const body: Trajectory = { ...snapshot, review: { ...snapshot.review, reviewed_at: new Date().toISOString() } };
      const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), keepalive: true,
      }).catch(() => null);
      if (!r?.ok) {
        dirty.current = true;
        setSave({ kind: "error", msg: r ? `HTTP ${r.status}` : "network error" });
        return;
      }
      const j = await r.json().catch(() => ({}));
      const at = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      if (j.rebased && j.trajectory) {
        adopting.current = true;
        setT(j.trajectory);
        const n = j.trajectory.review?.rebased?.dropped?.length ?? 0;
        setSave({ kind: "saved", at, msg: `recording was reprocessed; your edits were carried over${n ? ` (${n} no longer apply)` : ""}` });
      } else {
        setT((cur) => (cur.review.base_hash === j.base_hash ? cur : { ...cur, review: { ...cur.review, base_hash: j.base_hash } }));
        setSave(dirty.current ? { kind: "dirty" } : { kind: "saved", at });
      }
    })();
    inflight.current = run;
    try {
      await run;
    } finally {
      inflight.current = null;
    }
    if (dirty.current) await flush();
  }, [id]);

  useEffect(() => {
    if (adopting.current) {
      adopting.current = false;
      return;
    }
    if (!touched.current) return;
    dirty.current = true;
    setSave({ kind: "dirty" });
    const h = setTimeout(() => void flush(), 600);
    return () => clearTimeout(h);
  }, [t, flush]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty.current || inflight.current) {
        void flush();
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      if (dirty.current) void flush(); // leaving the page inside the app: save what's pending
    };
  }, [flush]);

  const resetReview = async () => {
    if (!confirm("Discard all review edits (reasoning, flags, checklist, AI suggestions, outcome) and reload the processor output?")) return;
    await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    location.reload();
  };

  // ---- replay sync ---------------------------------------------------------------
  const activeIdx = useMemo(() => {
    if (playhead == null) return null;
    let idx: number | null = null;
    for (let i = 0; i < t.steps.length; i++) if (t.steps[i].t_start <= playhead + 1e-3) idx = i;
    return idx;
  }, [playhead, t.steps]);

  useEffect(() => {
    if (playing && follow && activeIdx != null && activeIdx !== sel) setSel(activeIdx);
  }, [activeIdx, playing, follow, sel]);

  const seek = useCallback((time: number, play = false) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, time);
    setPlayhead(Math.max(0, time));
    if (play) v.play().catch(() => {});
  }, []);

  const selectStep = useCallback((s: Sel) => {
    setSel(s);
    if (typeof s === "number" && t.steps[s]) seek(Math.max(0, t.steps[s].t_start - 0.5));
    else if (s === "end" && t.duration_s) seek(Math.max(0, t.duration_s - 2));
  }, [seek, t.steps, t.duration_s]);

  // ---- navigation ---------------------------------------------------------------
  const flaggedIdx = useMemo(() => t.steps.map((s, i) => (s.flags.length ? i : -1)).filter((i) => i >= 0), [t]);
  const go = useCallback(
    (d: 1 | -1) => {
      const n = t.steps.length;
      const cur = sel;
      const next: Sel = cur === "end" ? (d < 0 && n ? n - 1 : "end") : cur + d >= n ? "end" : Math.max(0, cur + d);
      selectStep(next);
    },
    [t.steps.length, sel, selectStep],
  );
  const nextFlagged = useCallback(() => {
    const cur = sel === "end" ? -1 : sel;
    const nx = flaggedIdx.find((i) => i > cur) ?? flaggedIdx[0];
    if (nx !== undefined) selectStep(nx);
  }, [flaggedIdx, sel, selectStep]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight" || e.key === "j") go(1);
      else if (e.key === "ArrowLeft" || e.key === "k") go(-1);
      else if (e.key === "n") nextFlagged();
      else if (e.key === "e") selectStep("end");
      else if (e.key === "b") setView("before");
      else if (e.key === "a") setView("after");
      else if (e.key === " ") {
        if (tag === "VIDEO" || tag === "BUTTON") return;
        setView("replay");
        const v = video.current;
        if (v) (v.paused ? v.play() : Promise.resolve(v.pause())).catch(() => {});
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, nextFlagged, selectStep]);

  // ---- derived ----------------------------------------------------------------------
  const open = t.steps.flatMap((s) => s.flags);
  const openBy = (sev: string) => open.filter((f) => f.severity === sev).length;

  return (
    <div className="rv">
      <header className="rv-head">
        <div>
          <div className="eyebrow">
            <Link href="/">← recordings</Link> · {t.session_id}
            {t.duration_s != null && <> · {fmtTime(t.duration_s)}</>}
            {t.source?.legacy && <> · recorder 0.1</>}
          </div>
          <h1>{t.task || "Untitled task"}</h1>
          <div className="criteria">
            Done when:{" "}
            {t.success_criteria ? <b>{t.success_criteria}</b> : <span className="missing">no success criteria recorded</span>}
          </div>
        </div>
        <div className="rv-actions">
          <span className={`save-state ${["dirty", "error"].includes(save.kind) ? "dirty" : ""}`} aria-live="polite" title={save.msg}>
            {save.kind === "clean" && "No edits yet"}
            {save.kind === "dirty" && "Unsaved changes"}
            {save.kind === "saving" && "Saving…"}
            {save.kind === "saved" && `Review saved${save.at ? " " + save.at : ""}${save.msg ? " · " + save.msg : ""}`}
            {save.kind === "error" && <>Save failed ({save.msg}) · <button className="linkish" onClick={() => { dirty.current = true; void flush(); }}>retry</button></>}
          </span>
          <div className="seg" role="group" aria-label="Outcome (your decision)">
            {([["pass", "Task done"], ["fail", "Not done"], [null, "Undecided"]] as const).map(([v, label]) => (
              <button key={String(v)} className={v ?? "none"} aria-pressed={t.review.outcome === v}
                onClick={() => edit((d) => R.setOutcome(d, v))}>
                {label}
              </button>
            ))}
          </div>
          <button className="btn primary" onClick={() => setExportOpen(true)}>Export…</button>
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
          {rebased && t.review.rebased && (
            <span className="pill warn-pill" title={t.review.rebased.dropped.join("\n") || "all edits carried over"}>
              review carried over after reprocessing{t.review.rebased.dropped.length ? ` · ${t.review.rebased.dropped.length} dropped` : ""}
            </span>
          )}
        </div>
      </header>

      <Timeline t={t} sel={sel} onSelect={selectStep} playhead={t.media?.status === "ok" ? playhead ?? 0 : null}
        activeIdx={playing ? activeIdx : null} onSeek={t.media?.status === "ok" ? (x) => { setView("replay"); seek(x); } : undefined} />

      <div className="rv-body">
        <Stage ref={video} id={id} t={t} step={step} view={view} setView={setView} onTime={setPlayhead} onPlaying={setPlaying} />

        <aside className="side">
          <div className="step-nav">
            <div className="step-no">
              {sel === "end" ? <b>End state</b> : (
                <><b>#{String(sel).padStart(2, "0")}</b> <span style={{ color: "var(--muted)" }}>/ {t.steps.length}</span></>
              )}
              {step && <div style={{ color: "var(--muted)" }}>{fmtTime(step.t_start)} → {fmtTime(step.t_end)}</div>}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn" onClick={() => go(-1)} disabled={sel === 0 || !t.steps.length} aria-label="Previous step">←</button>
              <button className="btn" onClick={() => go(1)} disabled={sel === "end"} aria-label="Next step">→</button>
              <button className="btn" onClick={nextFlagged} disabled={!flaggedIdx.length}>Next flag</button>
            </div>
          </div>
          {t.media?.status === "ok" && (
            <label className="follow">
              <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow the replay
            </label>
          )}

          {step ? (
            <StepPanel t={t} index={sel as number} step={step} edit={edit} onJump={selectStep} aiStatus={aiStatus} />
          ) : (
            <EndPanel id={id} t={t} edit={edit} aiStatus={aiStatus} />
          )}

          <div className="keys">
            <kbd>←</kbd> <kbd>→</kbd> step · <kbd>n</kbd> next flag · <kbd>e</kbd> end state · <kbd>b</kbd>/<kbd>a</kbd> before/after · <kbd>space</kbd> replay
          </div>
        </aside>
      </div>
      {exportOpen && <ExportDialog ids={[id]} onClose={() => setExportOpen(false)} flush={flush} saveFailed={save.kind === "error"} />}
    </div>
  );
}
