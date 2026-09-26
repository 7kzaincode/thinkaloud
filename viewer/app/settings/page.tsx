"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { desktop } from "@/lib/desktop";

type KeyStatus = { stored: boolean; fromEnvironment: boolean; encryption: boolean };

export default function Settings() {
  const [bridge, setBridge] = useState<ReturnType<typeof desktop> | null>(null);
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [ai, setAi] = useState<{ configured: boolean; model: string; reason?: string | null } | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const b = desktop();
    setBridge(b ?? undefined);
    b?.apiKeyStatus().then(setStatus).catch(() => setStatus(null));
    fetch("/api/ai/status").then((r) => r.json()).then(setAi).catch(() => setAi(null));
  }, []);

  const save = async () => {
    if (!bridge) return;
    setBusy(true);
    setMsg(null);
    try {
      await bridge.setApiKey(key); // the app restarts its local server and reloads this page
      setKey("");
    } catch (e) {
      setMsg(String(e).replace(/^Error: (Error invoking remote method '[^']+': )?(Error: )?/, ""));
      setBusy(false);
    }
  };

  if (bridge === null) return null;
  return (
    <main className="record">
      <div className="eyebrow"><Link href="/">← recordings</Link> · settings</div>
      <h1>Settings</h1>
      <h2 className="sub">AI-assisted review</h2>
      <p className="lede">
        Optional. AI suggestions (narration checks, checklist drafts, final-screen checks) are sent to Anthropic only when you
        click a button in a review, and every suggestion needs your confirmation. Manual review works without it.
      </p>
      <div className="field">
        <span>Status</span>
        <div>{ai ? (ai.configured ? <b className="ok-text">Configured · model {ai.model}</b> : <span>Not configured: {ai.reason}</span>) : "…"}</div>
      </div>
      {!bridge ? (
        <p className="hint">In the browser version, set <code>ANTHROPIC_API_KEY</code> in the environment of the viewer server (see README).</p>
      ) : (
        <>
          {status?.fromEnvironment && <p className="hint">An <code>ANTHROPIC_API_KEY</code> from the environment is in use; a key saved here takes precedence.</p>}
          <label className="field">
            <span>Anthropic API key</span>
            <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
              placeholder={status?.stored ? "A key is saved (hidden). Paste a new one to replace it." : "sk-ant-…"} />
            <small>Stored on this computer, encrypted by Windows for your user account. It is never shown again or sent anywhere except the Anthropic API.</small>
          </label>
          {msg && <div className="alert">{msg}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn primary" disabled={busy || key.trim().length < 20} onClick={save}>{busy ? "Saving…" : "Save key"}</button>
            {status?.stored && <button className="btn ghost" disabled={busy} onClick={async () => { setBusy(true); await bridge.clearApiKey(); }}>Remove saved key</button>}
          </div>
        </>
      )}
      <h2 className="sub">Model</h2>
      <p className="hint">Defaults to <code>claude-opus-5</code>. Override with <code>THINKALOUD_AI_MODEL</code>; see README for the other settings.</p>
    </main>
  );
}
