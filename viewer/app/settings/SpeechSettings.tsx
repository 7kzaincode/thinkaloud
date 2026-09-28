"use client";

import { useEffect, useState } from "react";

interface ModelInfo { id: string; label: string; download_mb: number; note: string; downloaded: boolean }
interface State { speech_model: string; speech_model_source: "environment" | "settings" | "default"; speech_models: ModelInfo[] }

const size = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);

/** Which local speech-to-text model turns narration into text. */
export default function SpeechSettings() {
  const [st, setSt] = useState<State | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/settings").then((r) => r.json()).then(setSt).catch(() => setMsg("Could not load the speech settings."));
  }, []);

  const choose = async (id: string) => {
    setBusy(true);
    setMsg(null);
    const r = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ speech_model: id }),
    }).catch(() => null);
    const j = r ? await r.json().catch(() => null) : null;
    if (r?.ok && j) {
      setSt(j);
      setMsg("Saved. New recordings use it. To redo recordings you already processed, select them on the Recordings page "
        + "and click Process selected.");
    } else {
      setMsg(`Could not save: ${j?.error ?? "error"}`);
    }
    setBusy(false);
  };

  const env = st?.speech_model_source === "environment";
  return (
    <>
      <h2 className="sub">Speech recognition</h2>
      <p className="lede">
        How your narration is turned into text. It runs on this computer and nothing is uploaded. Each model is downloaded
        once, the first time it&apos;s used.
      </p>
      {!st ? <div className="none-yet">{msg ?? "…"}</div> : (
        <div className="field">
          <span>Model</span>
          <div className="choices" role="radiogroup" aria-label="Speech model">
            {st.speech_models.map((m) => (
              <button key={m.id} role="radio" aria-checked={st.speech_model === m.id} disabled={busy || env}
                className={`choice ${st.speech_model === m.id ? "on" : ""}`} onClick={() => st.speech_model !== m.id && choose(m.id)}>
                <b>{m.label}</b> <span className="mono faint">{m.id}</span>
                <span className="small">{m.note}</span>
                <span className="small faint">{m.downloaded ? "Downloaded" : `${size(m.download_mb)} download on first use`}</span>
              </button>
            ))}
          </div>
          {env && <small>Set by <code>THINKALOUD_WHISPER_MODEL</code> in the environment ({st.speech_model}).</small>}
          {!env && st.speech_model_source === "default" && <small>Accurate is the default.</small>}
          {msg && <small className="ok">{msg}</small>}
        </div>
      )}
    </>
  );
}
