"use client";

import { useState } from "react";

interface ExportResult {
  ok: boolean;
  bundle?: string;
  zip?: string;
  download?: string;
  formats?: string[];
  recordings?: number;
  assets?: number;
  validation?: { ok: boolean; errors: string[]; warnings: string[] };
  warnings?: string[];
  errors?: string[];
  error?: string;
}

export default function ExportDialog({ ids, onClose, dirty }: { ids: string[]; onClose: () => void; dirty: boolean }) {
  const [dataset, setDataset] = useState(true);
  const [claude, setClaude] = useState(true);
  const [media, setMedia] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ExportResult | null>(null);

  const run = async () => {
    setBusy(true);
    setRes(null);
    try {
      const r = await fetch("/api/export", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, formats: [dataset && "dataset", claude && "claude"].filter(Boolean), include_media: media }),
      });
      setRes(await r.json());
    } catch (e) {
      setRes({ ok: false, error: String(e) });
    } finally {
      setBusy(false);
    }
  };

  const validationErrors = res?.validation?.errors ?? [];
  const contentProblems = res?.errors ?? [];           // actions that can't be represented faithfully
  const allWarnings = [...(res?.warnings ?? []), ...(res?.validation?.warnings ?? [])];
  const headline = !res?.ok ? "" : validationErrors.length ? "Exported, but the bundle failed validation"
    : contentProblems.length ? "Exported and validated, with steps that could not be represented"
    : allWarnings.length ? "Exported and validated, with warnings" : "Exported and validated";

  return (
    <div className="modal-bg" role="dialog" aria-modal="true" aria-label="Export" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="eyebrow">Export {ids.length} recording{ids.length === 1 ? "" : "s"}</div>
        <h2>Export a validated bundle</h2>
        <label className="opt"><input type="checkbox" checked={dataset} onChange={(e) => setDataset(e.target.checked)} />
          <span><b>Dataset (vendor-neutral)</b> · thinkaloud.dataset/1.0: steps, before/after screens, narration with timing and provenance, review decisions</span></label>
        <label className="opt"><input type="checkbox" checked={claude} onChange={(e) => setClaude(e.target.checked)} />
          <span><b>Claude computer-use</b> · actions as <code>computer_toolset_20260801</code> tool calls with screenshot results; narration kept separately</span></label>
        <label className="opt"><input type="checkbox" checked={media} onChange={(e) => setMedia(e.target.checked)} />
          <span>Include replay video with narration audio (larger)</span></label>
        {dirty && <div className="alert soft">Your latest edits are still saving; the export uses what is saved.</div>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={onClose}>Close</button>
          <button className="btn primary" disabled={busy || (!dataset && !claude)} onClick={run}>{busy ? "Exporting and validating…" : "Export"}</button>
        </div>
        {res && (
          <div className={`export-result ${res.ok ? (validationErrors.length ? "bad" : contentProblems.length || allWarnings.length ? "warn" : "ok") : "bad"}`} aria-live="polite">
            {res.ok ? (
              <>
                <b>{headline}</b>
                <div className="small">{res.recordings} recording(s), {res.assets} file(s) · {res.bundle}</div>
                <div className="small">Bundle validation: {validationErrors.length ? `${validationErrors.length} error(s)` : "passed (checksums, schema, screenshot timing, coordinates, references)"}</div>
                {res.download && <a className="btn" href={res.download}>Download .zip</a>}
              </>
            ) : <b>Export failed: {res.error}</b>}
            {validationErrors.length > 0 && <ul className="errs">{validationErrors.slice(0, 30).map((e, i) => <li key={i}>{e}</li>)}</ul>}
            {contentProblems.length > 0 && (
              <div className="small">
                <b>Not representable ({contentProblems.length}):</b> these actions were left out of the Claude format and it is marked
                not valid for training; the dataset format keeps them.
                <ul className="errs">{contentProblems.slice(0, 30).map((e, i) => <li key={i}>{e}</li>)}</ul>
              </div>
            )}
            {allWarnings.length > 0 && (
              <details><summary>{allWarnings.length} warning(s)</summary>
                <ul className="warns">{allWarnings.slice(0, 100).map((w, i) => <li key={i}>{w}</li>)}</ul></details>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
