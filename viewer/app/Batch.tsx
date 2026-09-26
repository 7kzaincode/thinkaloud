"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { SessionSummary } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import ExportDialog from "./s/[id]/ExportDialog";

type Filter = "all" | "unprocessed" | "failed" | "unreviewed" | "pass" | "fail";
type SortKey = "date" | "steps" | "narration" | "words";

interface Job { id: string; state: string; total: number; done: number; failed: number; concurrency: number; started_at: string; finished_at?: string | null; runner: string }

const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

function status(s: SessionSummary): { label: string; cls: string } {
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

  const refresh = useCallback(async () => {
    const [a, b] = await Promise.all([
      fetch("/api/sessions").then((r) => r.json()).catch(() => null),
      fetch("/api/batch").then((r) => r.json()).catch(() => null),
    ]);
    if (a?.sessions) setRows(a.sessions);
    if (b?.jobs) setJobs(b.jobs);
  }, []);

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

  const process = async (ids: string[]) => {
    setMsg(null);
    const r = await fetch("/api/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, concurrency, force }) });
    const j = await r.json();
    setMsg(r.ok ? `Started job ${j.job_id} for ${ids.length} recording(s)` : `Could not start: ${j.error}`);
    setTimeout(refresh, 500);
  };

  const exportable = [...selected].filter((id) => rows.find((r) => r.id === id)?.processed);

  return (
    <main className="index wide">
      <div className="index-head">
        <div>
          <div className="eyebrow">thinkaloud · recordings</div>
          <h1>Recordings</h1>
        </div>
        <Link href="/record" className="btn primary big">New recording</Link>
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
        <button className="btn" disabled={!selected.size} onClick={() => process([...selected])}>Process selected ({selected.size})</button>
        <button className="btn" disabled={!rows.some((r) => !r.processed)} onClick={() => process(rows.filter((r) => !r.processed).map((r) => r.id))}>Process all new</button>
        <button className="btn" disabled={!exportable.length} onClick={() => setExporting(true)}>Export selected ({exportable.length})</button>
      </div>
      {msg && <div className="small faint" aria-live="polite">{msg}</div>}
      {jobs.filter((j) => j.state === "running").map((j) => (
        <div key={j.id} className="job">
          <span className="spin" /> job {j.id} ({j.runner}, {j.concurrency} worker{j.concurrency > 1 ? "s" : ""}): {j.done + j.failed}/{j.total} done{j.failed ? `, ${j.failed} failed` : ""}
          <div className="bar"><i style={{ width: `${((j.done + j.failed) / Math.max(1, j.total)) * 100}%` }} /></div>
        </div>
      ))}

      {rows.length === 0 ? (
        <div className="empty">
          No recordings yet. Click <b>New recording</b>{desktop ? "" : " (desktop app)"} or record from a terminal with <code>python recorder/record.py</code>,
          or generate the sample with <code>python scripts/make_synthetic.py</code>.
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
                    {s.processed ? <Link href={`/s/${encodeURIComponent(s.id)}`} className="task">{s.task || <em>Untitled task</em>}</Link>
                      : <span className="task">{s.task || <em>Untitled task</em>}</span>}
                    <div className="mono faint">{s.id}{s.duration_s != null ? ` · ${fmtTime(s.duration_s)}` : ""}</div>
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
      {exporting && <ExportDialog ids={exportable} onClose={() => setExporting(false)} dirty={false} />}
    </main>
  );
}
