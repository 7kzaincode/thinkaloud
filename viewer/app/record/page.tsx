"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { desktop, type Device } from "@/lib/desktop";

type Phase =
  | { kind: "setup" }
  | { kind: "recording"; t: number; events: number; countdown?: number; paused?: boolean }
  | { kind: "processing"; message: string }
  | { kind: "error"; message: string };

const DRAFT_KEY = "thinkaloud:draft";

export default function RecordPage() {
  const router = useRouter();
  const [bridge, setBridge] = useState<ReturnType<typeof desktop> | null>(null);
  const [task, setTask] = useState("");
  const [criteria, setCriteria] = useState("");
  const [devices, setDevices] = useState<Device[]>([]);
  const [device, setDevice] = useState<number | null>(null);
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "setup" });

  // Detect the desktop bridge on the client only, and restore the last draft.
  useEffect(() => {
    setBridge(desktop() ?? undefined);
    try {
      const d = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "{}");
      if (d.task) setTask(d.task);
      if (d.criteria) setCriteria(d.criteria);
    } catch {}
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ task, criteria }));
    } catch {}
  }, [task, criteria]);

  useEffect(() => {
    if (!bridge) return;
    bridge.devices().then((ds) => {
      setDevices(ds);
      setDevice((ds.find((d) => d.default) ?? ds[0])?.index ?? null);
    }).catch((e) => setPhase({ kind: "error", message: `Couldn't list microphones: ${e}` }));

    const offs = [
      bridge.onMeter((e) => {
        if (e.event === "level") {
          const v = Number(e.level) || 0;
          setLevel(v);
          if (v > 0.35) setHeard(true);
        }
      }),
      bridge.onRecorder((e) => {
        if (e.event === "countdown") setPhase({ kind: "recording", t: 0, events: 0, countdown: Number(e.n) });
        if (e.event === "started" || e.event === "status")
          setPhase({ kind: "recording", t: Number(e.t ?? 0), events: Number(e.events ?? 0), paused: !!e.paused });
        if (e.event === "paused" || e.event === "resumed")
          setPhase((p) => (p.kind === "recording" ? { ...p, paused: e.event === "paused" } : p));
        if (e.event === "error") setPhase({ kind: "error", message: String(e.message) });
      }),
      bridge.onProcess((e) => {
        if (e.event === "progress") setPhase({ kind: "processing", message: String(e.message) });
        if (e.event === "error") setPhase({ kind: "error", message: String(e.message) });
        if (e.event === "processed") {
          try { localStorage.removeItem(DRAFT_KEY); } catch {}
          router.push(`/s/${encodeURIComponent(String(e.session_id))}`);
        }
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [bridge, router]);

  // Live mic check while setting up.
  useEffect(() => {
    if (!bridge || phase.kind !== "setup" || device === null) return;
    setHeard(false);
    bridge.startMeter(device);
    return () => { bridge.stopMeter(); };
  }, [bridge, device, phase.kind]);

  const selected = devices.find((d) => d.index === device);
  const virtual = selected && /voicemod|virtual|cable|stereo mix/i.test(selected.name);
  const ready = task.trim().length > 3 && criteria.trim().length > 3 && device !== null;

  const start = async () => {
    if (!bridge || !ready) return;
    setPhase({ kind: "recording", t: 0, events: 0, countdown: 3 });
    try {
      await bridge.startRecording({ task: task.trim(), criteria: criteria.trim(), device });
    } catch (e) {
      setPhase({ kind: "error", message: String(e) });
    }
  };

  if (bridge === null) return null; // first client render

  return (
    <main className="record">
      <div className="eyebrow"><Link href="/">← sessions</Link> · new recording</div>

      {!bridge ? (
        <>
          <h1>Recording needs the desktop app</h1>
          <p className="lede">
            The browser can&apos;t capture clicks and keystrokes across your whole desktop. Open thinkaloud from the
            desktop app, or record from a terminal with <code>python recorder/record.py</code>.
          </p>
        </>
      ) : phase.kind === "setup" || phase.kind === "error" ? (
        <>
          <h1>What are you about to do?</h1>
          <p className="lede">
            You&apos;ll do a real task while saying out loud what you&apos;re doing and why. The window gets out of
            your way while you work.
          </p>

          <label className="field">
            <span>Task</span>
            <input value={task} onChange={(e) => setTask(e.target.value)} autoFocus
              placeholder="Find the cheapest nonstop flight from Toronto to San Francisco on Oct 17" />
          </label>
          <label className="field">
            <span>Done when</span>
            <input value={criteria} onChange={(e) => setCriteria(e.target.value)}
              placeholder="The cheapest nonstop flight is selected on the checkout page" />
            <small>A reviewer checks the final screen against this. Be concrete.</small>
          </label>

          <div className="field">
            <span>Microphone</span>
            <div className="mic-row">
              <select value={device ?? ""} onChange={(e) => setDevice(Number(e.target.value))} aria-label="Microphone">
                {devices.map((d) => (
                  <option key={d.index} value={d.index}>{d.name}{d.default ? " (default)" : ""}</option>
                ))}
              </select>
              <div className="meter" aria-label="Microphone level"><i style={{ width: `${Math.round(level * 100)}%` }} /></div>
            </div>
            <small className={virtual ? "warn" : heard ? "ok" : ""}>
              {virtual
                ? "This looks like a virtual device. Pick your real microphone."
                : heard ? "We can hear you." : "Say something to check the level."}
            </small>
          </div>

          {phase.kind === "error" && <div className="alert">{phase.message}</div>}

          <div className="record-go">
            <button className="btn primary big" onClick={start} disabled={!ready}>Start recording</button>
            <span className="hint">3-second countdown. Stop with <kbd>F9</kbd> or the Stop button at the bottom of the screen.</span>
          </div>

          <ul className="tips">
            <li><b>Say it before you do it.</b> &ldquo;I&apos;m sorting by price because the default is sponsored.&rdquo;</li>
            <li><b>Explain choices, not clicks.</b> Why this option and not the others?</li>
            <li><b>Pause for anything private.</b> F8 or the Pause button stops all capture until you resume.</li>
            <li><b>Password fields are masked</b> when Windows reports them as password fields. Other secrets are only redacted if we can detect them.</li>
          </ul>
        </>
      ) : phase.kind === "recording" ? (
        <div className="status">
          <div className="big-dot" />
          <h1>{phase.countdown ? `Starting in ${phase.countdown}…` : phase.paused ? "Paused" : "Recording"}</h1>
          <p className="lede">
            {phase.countdown ? "Switch to the window you'll work in." :
              `${Math.floor(phase.t / 60)}:${String(Math.floor(phase.t % 60)).padStart(2, "0")} · ${phase.events} events. Press F9 or Stop when you're done.`}
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            {!phase.countdown && <button className="btn" onClick={() => bridge.pauseRecording(!phase.paused)}>{phase.paused ? "Resume" : "Pause"} (F8)</button>}
            <button className="btn" onClick={() => bridge.stopRecording()}>Stop recording (F9)</button>
          </div>
          {phase.paused && <p className="hint">Nothing is captured while paused: no input, no screen, silence instead of audio.</p>}
        </div>
      ) : (
        <div className="status">
          <div className="spinner" />
          <h1>Processing</h1>
          <p className="lede">{phase.message}</p>
          <small className="hint">
            The first time a speech model is used it is downloaded once (up to 1.6 GB for Accurate; see Settings).
            After that, transcribing takes a fraction of the recording&apos;s length.
          </small>
        </div>
      )}
    </main>
  );
}
