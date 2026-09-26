"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { desktop, type AiProvider, type ApiKeyStatus } from "@/lib/desktop";

type AiStatus = { configured: boolean; provider?: string; provider_name?: string; model: string; reason?: string | null };

const PROVIDERS: { id: AiProvider; name: string; env: string; placeholder: string; model: string }[] = [
  { id: "anthropic", name: "Anthropic (Claude)", env: "ANTHROPIC_API_KEY", placeholder: "sk-ant-…", model: "claude-opus-5" },
  { id: "gemini", name: "Google Gemini", env: "GEMINI_API_KEY", placeholder: "Gemini API key", model: "gemini-3.5-flash" },
];

export default function Settings() {
  const [bridge, setBridge] = useState<ReturnType<typeof desktop> | null>(null);
  const [status, setStatus] = useState<ApiKeyStatus | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [keys, setKeys] = useState<Record<AiProvider, string>>({ anthropic: "", gemini: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const b = desktop();
    setBridge(b ?? undefined);
    b?.apiKeyStatus().then(setStatus).catch(() => setStatus(null));
    fetch("/api/ai/status").then((r) => r.json()).then(setAi).catch(() => setAi(null));
  }, []);

  // every change restarts the app's local server and reloads this page
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg(String(e).replace(/^Error: (Error invoking remote method '[^']+': )?(Error: )?/, ""));
      setBusy(false);
    }
  };

  if (bridge === null) return null;
  const choice = status?.provider ?? null;
  return (
    <main className="record">
      <div className="eyebrow"><Link href="/">← recordings</Link> · settings</div>
      <h1>Settings</h1>
      <h2 className="sub">AI-assisted review</h2>
      <p className="lede">
        Optional. AI suggestions (narration checks, checklist drafts, final-screen checks) are sent to the provider below only
        when you click a button in a review, and every suggestion needs your confirmation. Manual review works without it.
      </p>
      <div className="field">
        <span>Status</span>
        <div>{ai ? (ai.configured
          ? <b className="ok-text">Configured · {ai.provider_name} · model {ai.model}</b>
          : <span>Not configured: {ai.reason}</span>) : "…"}</div>
      </div>
      {!bridge ? (
        <p className="hint">
          In the browser version, set <code>ANTHROPIC_API_KEY</code> or <code>GEMINI_API_KEY</code> in the environment of the
          viewer server, and optionally <code>THINKALOUD_AI_PROVIDER</code> (<code>anthropic</code> or <code>gemini</code>); see README.
        </p>
      ) : (
        <>
          <div className="field">
            <span>Provider</span>
            <div className="seg" role="radiogroup" aria-label="AI provider">
              {([[null, "Automatic"], ...PROVIDERS.map((p) => [p.id, p.name])] as [AiProvider | null, string][]).map(([id, name]) => (
                <button key={name} role="radio" aria-checked={choice === id} aria-pressed={choice === id} disabled={busy}
                  onClick={() => choice !== id && act(() => bridge.setAiProvider(id))}>{name}</button>
              ))}
            </div>
            <small>Automatic uses Anthropic when it has a key, otherwise Gemini.</small>
          </div>
          {PROVIDERS.map((p) => {
            const k = status?.keys[p.id];
            return (
              <label key={p.id} className="field">
                <span>{p.name} API key</span>
                <input type="password" autoComplete="off" value={keys[p.id]}
                  onChange={(e) => setKeys((o) => ({ ...o, [p.id]: e.target.value }))}
                  placeholder={k?.stored ? "A key is saved (hidden). Paste a new one to replace it." : p.placeholder} />
                <small>
                  {k?.fromEnvironment && !k.stored ? <>A key from the environment (<code>{p.env}</code>) is in use; a key saved here takes precedence. </> : null}
                  Default model <code>{p.model}</code>.
                </small>
                <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                  <button className="btn primary" disabled={busy || keys[p.id].trim().length < 20}
                    onClick={(e) => { e.preventDefault(); act(async () => { await bridge.setApiKey(p.id, keys[p.id]); setKeys((o) => ({ ...o, [p.id]: "" })); }); }}>
                    {busy ? "Saving…" : "Save key"}
                  </button>
                  {k?.stored && (
                    <button className="btn ghost" disabled={busy} onClick={(e) => { e.preventDefault(); act(() => bridge.clearApiKey(p.id)); }}>Remove saved key</button>
                  )}
                </div>
              </label>
            );
          })}
          <p className="hint">
            Keys are stored on this computer, encrypted by Windows for your user account, and never shown again or sent anywhere
            except that provider&apos;s API.
          </p>
          {msg && <div className="alert">{msg}</div>}
        </>
      )}
      <p className="hint">
        Check your provider&apos;s data-use terms before sending recordings: for example, Google may use content sent with
        free-tier (unpaid) Gemini API keys to improve its products. Override the model with <code>THINKALOUD_AI_MODEL</code>; see
        README for the other settings.
      </p>
    </main>
  );
}
