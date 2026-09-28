"use client";

import { useState, type ReactNode } from "react";

function Modal({ label, onClose, children, onSubmit }: {
  label: string; onClose: () => void; children: ReactNode; onSubmit: (e: React.FormEvent) => void;
}) {
  return (
    <div className="modal-bg" role="dialog" aria-modal="true" aria-label={label} onClick={onClose}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={onSubmit}>{children}</form>
    </div>
  );
}

/**
 * Change a recording's task and "done when" text. `prepare` runs first (the review page saves
 * pending edits, so nothing is lost when it reloads with the new text).
 */
export function EditDetailsDialog({ id, task, criteria, onClose, onSaved, prepare }: {
  id: string; task: string; criteria: string; onClose: () => void;
  onSaved: (r: { task: string; success_criteria: string; changed: boolean }) => void;
  prepare?: () => Promise<boolean>;
}) {
  const [tk, setTk] = useState(task);
  const [cr, setCr] = useState(criteria);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (prepare && !(await prepare())) {
        setErr("Your latest review edits could not be saved, so nothing was changed. Retry the save at the top of the page first.");
        return;
      }
      const r = await fetch(`/api/sessions/${encodeURIComponent(id)}/details`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: tk, success_criteria: cr }),
      }).catch(() => null);
      const j = r ? await r.json().catch(() => ({})) : {};
      if (!r?.ok || !j.ok) {
        setErr(j.error ?? (r ? `HTTP ${r.status}` : "network error"));
        return;
      }
      onSaved(j);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal label="Edit recording" onClose={onClose} onSubmit={save}>
      <div className="eyebrow mono">{id}</div>
      <h2>Edit the task and &ldquo;done when&rdquo;</h2>
      <label className="field">
        <span>Task</span>
        <input id="edit-task" value={tk} onChange={(e) => setTk(e.target.value)} maxLength={1000} autoFocus />
      </label>
      <label className="field">
        <span>Done when</span>
        <input id="edit-criteria" value={cr} onChange={(e) => setCr(e.target.value)} maxLength={1000} />
        <small>A reviewer checks the final screen against this. The text as first recorded is kept in the recording.</small>
      </label>
      {err && <div className="alert">{err}</div>}
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy || !tk.trim()}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </Modal>
  );
}

export interface DeletableRecording { id: string; task: string }

/** Move recordings to Recently deleted (restorable from the Recordings page). */
export function DeleteDialog({ items, onClose, onDone }: {
  items: DeletableRecording[]; onClose: () => void;
  onDone: (r: { moved: string[]; refused: { id: string; reason: string }[] }) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const one = items.length === 1;

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/trash", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: items.map((i) => i.id) }),
      }).catch(() => null);
      const j = r ? await r.json().catch(() => ({})) : {};
      if (!r?.ok) {
        setErr(j.error ?? (r ? `HTTP ${r.status}` : "network error"));
        return;
      }
      onDone({ moved: j.moved ?? [], refused: j.refused ?? [] });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal label="Delete recordings" onClose={onClose} onSubmit={run}>
      <h2>{one ? "Delete this recording?" : `Delete ${items.length} recordings?`}</h2>
      <ul className="del-list">
        {items.slice(0, 8).map((i) => <li key={i.id}><b>{i.task || "Untitled task"}</b> <span className="mono faint">{i.id}</span></li>)}
        {items.length > 8 && <li className="faint">and {items.length - 8} more</li>}
      </ul>
      <p className="small" style={{ margin: 0 }}>
        {one ? "It moves" : "They move"} to <b>Recently deleted</b> at the bottom of this page, with {one ? "its" : "their"} review.
        You can restore {one ? "it" : "them"} from there until you empty it.
      </p>
      {err && <div className="alert">{err}</div>}
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={onClose} autoFocus>Cancel</button>
        <button type="submit" className="btn danger" disabled={busy}>{busy ? "Deleting…" : "Delete"}</button>
      </div>
    </Modal>
  );
}

/** A yes/no question inside the page (used for permanent deletion). */
export function ConfirmDialog({ title, children, confirmLabel, onConfirm, onClose }: {
  title: string; children: ReactNode; confirmLabel: string; onConfirm: () => Promise<void>; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal label={title} onClose={onClose} onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      try { await onConfirm(); } finally { setBusy(false); }
    }}>
      <h2>{title}</h2>
      <div className="small">{children}</div>
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={onClose} autoFocus>Cancel</button>
        <button type="submit" className="btn danger" disabled={busy}>{busy ? "Deleting…" : confirmLabel}</button>
      </div>
    </Modal>
  );
}
