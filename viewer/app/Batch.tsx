"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { SessionSummary } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { desktop as desktopBridge } from "@/lib/desktop";
import ExportDialog from "./s/[id]/ExportDialog";
import { ConfirmDialog, DeleteDialog, EditDetailsDialog } from "./RecordingDialogs";

type Filter = "all" | "unprocessed" | "failed" | "unreviewed" | "pass" | "fail";
type SortKey = "date" | "steps" | "narration" | "words";

interface TrashItem { name: string; id: string; task: string; deleted_at: string | null }
interface Job { id: string; state: string; total: number; done: number; failed: number; concurrency: number; started_at: string; finished_at?: string | null; runner: string }

const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

function status(s: SessionSummary): { label: string; cls: string } {
  if (s.in_progress) return { label: "recording…", cls: "run" };
  const p = s.processing?.state;
  if (p === "running") return { label: "processing", cls: "run" };
  if (p === "queued") return { label: "queued", cls: "run" };
  if (p === "failed") return { label: "failed", cls: "bad" };
  if (p === "interrupted") return { label: "interrupted", cls: "bad" };
  if (!s.processed) return { label: "not processed", cls: "idle" };
  return { label: s.legacy ? "processed · 0.1" : "processed", cls: "ok" };
}

export default function Batch({ initial, desktop }: { initial: SessionSummary[]; desktop: boolean }) {
  const [rows, setRows] = useState(initial);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<SortKey>("date");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [concurrency, setConcurrency] = useState(2);
  const [force, setForce] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [editing, setEditing] = useState<SessionSummary | null>(null);
  const [deleting, setDeleting] = useState<SessionSummary[] | null>(null);
  const [trash, setTrash] = useState<TrashItem[]>([]);
  const [undo, setUndo] = useState<string[] | null>(null);   // ids just deleted
  const [purging, setPurging] = useState<TrashItem | "all" | null>(null);
  const [bridge, setBridge] = useState<ReturnType<typeof desktopBridge>>(undefined);
  useEffect(() => setBridge(desktopBridge()), []);

  const refresh = useCallback(async () => {
    const [a, b, c] = await Promise.all([
      fetch("/api/sessions").then((r) => r.json()).catch(() => null),
      fetch("/api/batch").then((r) => r.json()).catch(() => null),
      fetch("/api/trash").then((r) => r.json()).catch(() => null),
    ]);
    if (a?.sessions) setRows(a.sessions);
    if (b?.jobs) setJobs(b.jobs);
    if (c?.items) setTrash(c.items);
  }, []);

  const restore = async (names: string[]) => {
    const errors: string[] = [];
    for (const n of names) {
      const r = await fetch(`/api/trash/${encodeURIComponent(n)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "restore" }),
      }).catch(() => null);
      if (!r?.ok) errors.push(`${n}: ${(await r?.json().catch(() => null))?.error ?? "could not restore"}`);
    }
    setUndo(null);
    setMsg(errors.length ? `Could not restore ${errors.join("; ")}` : `Restored ${names.length} recording(s)`);
    await refresh();
  };

  const undoDelete = async (ids: string[]) => {
    const items: TrashItem[] = (await fetch("/api/trash").then((r) => r.json()).catch(() => null))?.items ?? [];
    // the newest deleted copy of each id (the list is newest first)
    await restore(ids.map((id) => items.find((i) => i.id === id)?.name).filter((n): n is string => !!n));
  };

  const purge = async (item: TrashItem | "all") => {
    const r = await fetch(item === "all" ? "/api/trash" : `/api/trash/${encodeURIComponent(item.name)}`, { method: "DELETE" }).catch(() => null);
    setPurging(null);
    setUndo(null);
    setMsg(r?.ok ? (item === "all" ? "Recently deleted emptied" : "Deleted permanently") : "Could not delete: " + ((await r?.json().catch(() => null))?.error ?? "error"));
    await refresh();
  };

  const busy = rows.some((r) => r.processing?.state === "running" || r.processing?.state === "queued") || jobs.some((j) => j.state === "running");
  useEffect(() => {
    refresh();
    const h = setInterval(refresh, busy ? 1500 : 8000);
    return () => clearInterval(h);
  }, [refresh, busy]);

  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase();
    let r = rows.filter((s) => !ql || s.task.toLowerCase().includes(ql) || s.id.toLowerCase().includes(ql));
    r = r.filter((s) => {
      switch (filter) {
        case "unprocessed": return !s.processed;
        case "failed": return s.processing?.state === "failed" || s.processing?.state === "interrupted" || !!s.error;
        case "unreviewed": return s.processed && !s.outcome;
        case "pass": return s.outcome === "pass";
        case "fail": return s.outcome === "fail";
        default: return true;
      }
    });
    const key = (s: SessionSummary) =>
      sort === "steps" ? s.metrics?.steps ?? -1 : sort === "narration" ? s.metrics?.pct_narrated ?? -1 : sort === "words" ? s.metrics?.avg_words_per_step ?? -1 : 0;
    return sort === "date" ? r : [...r].sort((a, b) => key(b) - key(a));
  }, [rows, q, filter, sort]);

  const totals = useMemo(() => {
    const processed = rows.filter((r) => r.metrics);
    const steps = processed.reduce((n, r) => n + r.metrics!.steps, 0);
    const narrated = processed.reduce((n, r) => n + r.metrics!.narrated_steps, 0);
    const wordsN = processed.reduce((n, r) => n + r.metrics!.narration_words, 0);
    return {
      n: rows.length, processed: processed.length, steps, narrated, pctNarrated: steps ? narrated / steps : null,
      wordsPerStep: steps ? wordsN / steps : null,
      pass: rows.filter((r) => r.outcome === "pass").length, fail: rows.filter((r) => r.outcome === "fail").length,
      undecided: rows.filter((r) => r.processed && !r.outcome).length,
    };
  }, [rows]);

  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const allShownSelected = shown.length > 0 && shown.every((s) => selected.has(s.id));

  const [starting, setStarting] = useState(false);
  const process = async (ids: string[]) => {
    if (starting) return;
    setStarting(true);
    setMsg(null);
    setUndo(null);
    try {
      const r = await fetch("/api/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, concurrency, force }) })
        .catch(() => null);
      const j = r ? await r.json().catch(() => ({ error: `HTTP ${r.status}` })) : { error: "network error" };
      const busy: string[] = Array.isArray(j.busy) ? j.busy : [];
      const n = ids.length - busy.length;
      setMsg(r?.ok
        ? `Started job ${j.job_id} for ${n} recording(s)${busy.length ? `; ${busy.length} already being processed` : ""}`
        : `Could not start: ${j.error}${busy.length ? ` (${busy.length} already being processed)` : ""}`);
    } finally {
      setStarting(false);
      setTimeout(refresh, 500);
    }
  };

  const exportable = [...selected].filter((id) => rows.find((r) => r.id === id)?.processed);
  const inFlight = (r: SessionSummary) => r.processing?.state === "running" || r.processing?.state === "queued";
  const newIds = rows.filter((r) => !r.processed && !inFlight(r) && !r.in_progress).map((r) => r.id);
  const selectable = rows.filter((r) => selected.has(r.id) && !inFlight(r) && !r.in_progress).map((r) => r.id);
  const deletable = (r: SessionSummary) => !r.readonly && !r.in_progress && !inFlight(r);
  const selectedDeletable = rows.filter((r) => selected.has(r.id) && deletable(r));

  return (
    <main className="index wide">
      <div className="index-head">
        <div>
          <div className="eyebrow">thinkaloud · recordings</div>
          <h1>Recordings</h1>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Link href="/settings" className="btn big">Settings</Link>
          <Link href="/record" className="btn primary big">New recording</Link>
        </div>
      </div>
      <p className="lede">
        Each recording is one expert doing one task while narrating. Process it, step through what they did and why,
        decide whether the task was actually done, then export validated data.
      </p>

      <div className="stats" aria-label="Totals">
        <div><b>{totals.n}</b><span>recordings</span></div>
        <div><b>{totals.processed}</b><span>processed</span></div>
        <div><b>{totals.steps}</b><span>steps</span></div>
        <div title="steps with their own narration ÷ all steps, over processed recordings"><b>{pct(totals.pctNarrated)}</b><span>steps narrated</span></div>
        <div title="narration words attached to steps ÷ all steps"><b>{totals.wordsPerStep == null ? "—" : totals.wordsPerStep.toFixed(1)}</b><span>words / step</span></div>
        <div title="human decisions only; AI suggestions never count"><b>{totals.pass} / {totals.fail} / {totals.undecided}</b><span>done / not done / undecided</span></div>
      </div>

      <div className="toolbar">
        <input className="search" placeholder="Search task or id" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search recordings" />
        <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)} aria-label="Filter">
          <option value="all">All</option><option value="unprocessed">Not processed</option><option value="failed">Processing failed</option>
          <option value="unreviewed">Not decided</option><option value="pass">Task done</option><option value="fail">Not done</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort">
          <option value="date">Newest</option><option value="steps">Most steps</option><option value="narration">Most narrated</option><option value="words">Most words / step</option>
        </select>
        <span className="spacer" />
        <label className="small">Workers <select value={concurrency} onChange={(e) => setConcurrency(+e.target.value)} aria-label="Concurrent workers">
          {[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}</select></label>
        <label className="small"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> reprocess up-to-date</label>
        <button className="btn" disabled={!selectable.length || starting} onClick={() => process(selectable)}
          title={selectable.length < selected.size ? "Recordings already being processed are left out" : undefined}>
          Process selected ({selectable.length})
        </button>
        <button className="btn" disabled={!newIds.length || starting} onClick={() => process(newIds)}>Process all new ({newIds.length})</button>
        <button className="btn" disabled={!exportable.length} onClick={() => setExporting(true)}>Export selected ({exportable.length})</button>
        <button className="btn ghost" disabled={!selectedDeletable.length} onClick={() => setDeleting(selectedDeletable)}
          title={selectedDeletable.length < selected.size ? "Samples and recordings being processed are left out" : undefined}>
          Delete selected ({selectedDeletable.length})
        </button>
      </div>
      {(msg || undo) && (
        <div className="small faint" aria-live="polite">
          {msg}
          {undo && <> · <button className="linkish" onClick={() => undoDelete(undo)}>Undo</button></>}
        </div>
      )}
      {jobs.filter((j) => j.state === "running").map((j) => (
        <div key={j.id} className="job">
          <span className="spin" /> job {j.id} ({j.runner}, {j.concurrency} worker{j.concurrency > 1 ? "s" : ""}): {j.done + j.failed}/{j.total} done{j.failed ? `, ${j.failed} failed` : ""}
          <div className="bar"><i style={{ width: `${((j.done + j.failed) / Math.max(1, j.total)) * 100}%` }} /></div>
        </div>
      ))}

      {rows.length === 0 ? (
        <div className="empty">
          {desktop ? (
            <>No recordings yet. Click <b>New recording</b>, type the task and what “done” looks like, and talk through what you do.</>
          ) : (
            <>
              No recordings yet. Click <b>New recording</b> (desktop app) or record from a terminal with <code>python recorder/record.py</code>,
              or generate the sample with <code>python scripts/make_synthetic.py</code>.
            </>
          )}
        </div>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th><input type="checkbox" aria-label="Select all shown" checked={allShownSelected}
                onChange={() => setSelected(allShownSelected ? new Set() : new Set(shown.map((s) => s.id)))} /></th>
              <th>Recording</th><th>Status</th><th className="num">Steps</th>
              <th className="num" title="steps with their own narration (count and % of steps)">Narrated</th>
              <th className="num" title="narration words attached to steps ÷ all steps">Words / step</th>
              <th className="num" title="open QC/reviewer flags: privacy · attention">Flags</th>
              <th title="human decision and checklist items marked met">Review</th>
              <th>Warnings</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => {
              const st = status(s);
              const m = s.metrics;
              return (
                <tr key={s.id} className={selected.has(s.id) ? "sel" : ""}>
                  <td><input type="checkbox" checked={selected.has(s.id)} onChange={() => toggle(s.id)} aria-label={`Select ${s.id}`} /></td>
                  <td>
                    <Link href={`/s/${encodeURIComponent(s.id)}`} className="task">{s.task || <em>Untitled task</em>}</Link>
                    <div className="mono faint">{s.id}{s.duration_s != null ? ` · ${fmtTime(s.duration_s)}` : ""}{s.readonly ? " · sample" : ""}</div>
                    {!s.readonly && !s.in_progress && (
                      <div className="row-actions">
                        <button className="linkish" onClick={() => setEditing(s)} disabled={inFlight(s)}
                          title={inFlight(s) ? "Wait until processing has finished" : undefined}>Edit</button>
                        {bridge?.showRecording && <button className="linkish" onClick={() => bridge.showRecording!(s.id)}>Show in folder</button>}
                        <button className="linkish danger-text" onClick={() => setDeleting([s])} disabled={inFlight(s)}
                          title={inFlight(s) ? "Wait until processing has finished" : undefined}>Delete</button>
                      </div>
                    )}
                  </td>
                  <td><span className={`status ${st.cls}`}>{st.label}</span>
                    {(s.processing?.state === "failed" || s.processing?.state === "interrupted") && <div className="small bad-text" title={s.processing.error ?? ""}>{(s.processing.error ?? "").slice(0, 60)}</div>}
                    {s.processing?.attempts ? <div className="small faint">{s.processing.attempts} attempt(s)</div> : null}
                  </td>
                  <td className="num">{m?.steps ?? "—"}</td>
                  <td className="num">{m ? <>{m.narrated_steps} <span className="faint">({pct(m.pct_narrated)})</span></> : "—"}</td>
                  <td className="num">{m?.avg_words_per_step == null ? "—" : m.avg_words_per_step.toFixed(1)}</td>
                  <td className="num">{m ? <>{m.open_flags.high ? <span className="bad-text">{m.open_flags.high}</span> : 0} · {m.open_flags.warn}</> : "—"}</td>
                  <td>
                    {s.outcome ? <span className={`pill ${s.outcome}`}>{s.outcome === "pass" ? "Task done" : "Not done"}</span>
                      : <span className="pill">{s.processed ? (s.reviewed ? "In review" : "Not reviewed") : "—"}</span>}
                    {m && m.checklist.items > 0 && <div className="small faint">{m.checklist.met}/{m.checklist.items} items met</div>}
                    {m && m.ai_pending > 0 && <div className="small faint">{m.ai_pending} AI suggestion(s) pending</div>}
                  </td>
                  <td className="small">{s.error ? <span className="bad-text">{s.error}</span> : s.warnings.length ? <span title={s.warnings.join("\n")}>{s.warnings.slice(0, 2).join("; ")}{s.warnings.length > 2 ? ` +${s.warnings.length - 2}` : ""}</span> : <span className="faint">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="footnote">
        Narrated = steps with at least one narration segment of their own (carried or reviewer-written reasoning doesn't count).
        Words / step = narration words attached to steps ÷ all steps. Review counts are human decisions only.
      </p>
      {trash.length > 0 && (
        <details className="trash">
          <summary>Recently deleted ({trash.length})</summary>
          <p className="small faint">Deleted recordings stay here, with their reviews, until you delete them permanently.</p>
          <table className="grid">
            <tbody>
              {trash.map((i) => (
                <tr key={i.name}>
                  <td>
                    <div>{i.task || <em>Untitled task</em>}</div>
                    <div className="mono faint">{i.id}{i.deleted_at ? ` · deleted ${new Date(i.deleted_at).toLocaleString()}` : ""}</div>
                  </td>
                  <td className="trash-actions">
                    <button className="btn" onClick={() => restore([i.name])}>Restore</button>
                    <button className="btn ghost" onClick={() => setPurging(i)}>Delete permanently</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="btn ghost danger-text" onClick={() => setPurging("all")}>Empty Recently deleted</button>
        </details>
      )}
      {exporting && <ExportDialog ids={exportable} onClose={() => setExporting(false)} />}
      {editing && (
        <EditDetailsDialog id={editing.id} task={editing.task} criteria={editing.criteria ?? ""} onClose={() => setEditing(null)}
          onSaved={(r) => { setEditing(null); setMsg(r.changed ? "Saved" : "Nothing changed"); void refresh(); }} />
      )}
      {purging && (
        <ConfirmDialog title={purging === "all" ? `Permanently delete ${trash.length} recording(s)?` : "Permanently delete this recording?"}
          confirmLabel="Delete permanently" onClose={() => setPurging(null)} onConfirm={() => purge(purging)}>
          {purging !== "all" && <p style={{ margin: "0 0 8px" }}><b>{purging.task || "Untitled task"}</b> <span className="mono faint">{purging.id}</span></p>}
          The screen recording, audio, screenshots, transcript and review are erased from this computer. This can&apos;t be undone.
        </ConfirmDialog>
      )}
      {deleting && (
        <DeleteDialog items={deleting} onClose={() => setDeleting(null)}
          onDone={(r) => {
            setDeleting(null);
            setSelected((sel) => { const n = new Set(sel); r.moved.forEach((id) => n.delete(id)); return n; });
            setMsg(`Deleted ${r.moved.length} recording(s)` + (r.refused.length ? `; not deleted: ${r.refused.map((x) => `${x.id} (${x.reason})`).join(", ")}` : ""));
            setUndo(r.moved.length ? r.moved : null);
            void refresh();
          }} />
      )}
    </main>
  );
}
