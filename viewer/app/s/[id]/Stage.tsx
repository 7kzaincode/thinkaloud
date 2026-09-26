"use client";

import { forwardRef } from "react";
import type { Observation, Step, Trajectory } from "@/lib/types";
import { STATUS_TEXT, fmtOffset, fmtTime, scrollRuns, targetReliable } from "@/lib/format";

export type View = "before" | "after" | "replay";

interface Props {
  id: string;
  t: Trajectory;
  step: Step | null;
  view: View;
  setView: (v: View) => void;
  onTime: (t: number) => void;
  onPlaying: (p: boolean) => void;
}

const frameUrl = (id: string, file: string) => `/api/sessions/${encodeURIComponent(id)}/frame/${file}`;

const Stage = forwardRef<HTMLVideoElement, Props>(function Stage({ id, t, step, view, setView, onTime, onPlaying }, videoRef) {
  const media = t.media;
  const hasMedia = media?.status === "ok" && !!media.file;
  const obs: Observation | undefined = step
    ? view === "after" ? step.observations?.after : step.observations?.before
    : t.final_observation;
  const W = t.screen.w || 1, H = t.screen.h || 1;
  const a = step?.action;
  const showMarker = step && view === "before" && a && (a.type === "click" || a.type === "scroll" || a.type === "drag");
  const rect = step && view === "before" && targetReliable(step) ? step.target?.frame_rect : null;

  return (
    <section className="stage">
      <div className="stage-bar">
        <div className="seg" role="tablist" aria-label="What to show">
          {step ? (
            <>
              <button role="tab" aria-selected={view === "before"} aria-pressed={view === "before"} className="none" onClick={() => setView("before")}>
                Before <Dot status={step.observations?.before.status} />
              </button>
              <button role="tab" aria-selected={view === "after"} aria-pressed={view === "after"} className="none" onClick={() => setView("after")}>
                After <Dot status={step.observations?.after.status} />
              </button>
            </>
          ) : (
            <button role="tab" aria-selected={view !== "replay"} aria-pressed={view !== "replay"} className="none" onClick={() => setView("before")}>Final screen</button>
          )}
          <button role="tab" aria-selected={view === "replay"} aria-pressed={view === "replay"} className="none" onClick={() => setView("replay")}>
            Replay
          </button>
        </div>
        <span className="hint">
          <kbd>b</kbd> before · <kbd>a</kbd> after · <kbd>space</kbd> replay
        </span>
      </div>

      <div className="shot-wrap" style={{ display: view === "replay" ? "none" : undefined }}>
        <div className="shot">
          {obs?.file ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={frameUrl(id, obs.file)} alt={step ? `Screen ${view} step ${step.id}` : "Final screen"} />
          ) : (
            <div className="noshot">
              <b>{step ? (view === "after" ? "No after-state for this step" : "No before-state for this step") : "No final screenshot"}</b>
              <span>{obs?.reason ?? STATUS_TEXT.missing}</span>
              {hasMedia && <span>The replay may still show this moment.</span>}
            </div>
          )}
          {obs?.file && rect && (
            <div className="target-box" style={{ left: `${(rect[0] / W) * 100}%`, top: `${(rect[1] / H) * 100}%`, width: `${(rect[2] / W) * 100}%`, height: `${(rect[3] / H) * 100}%` }} />
          )}
          {obs?.file && showMarker && (
            <div className={`marker ${a!.type === "scroll" ? "scroll" : ""}`}
              style={{ left: `${((a as { x: number }).x / W) * 100}%`, top: `${((a as { y: number }).y / H) * 100}%` }} />
          )}
          {obs?.file && showMarker && a!.type === "drag" && (
            <>
              <svg className="drag-path" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
                <line x1={a.x} y1={a.y} x2={a.x2} y2={a.y2} />
              </svg>
              <div className="marker end" style={{ left: `${(a.x2 / W) * 100}%`, top: `${(a.y2 / H) * 100}%` }} />
            </>
          )}
        </div>
      </div>

      <div className="player" style={{ display: view === "replay" ? undefined : "none" }}>
        {hasMedia ? (
          <video
            ref={videoRef}
            src={`/api/sessions/${encodeURIComponent(id)}/media/${encodeURIComponent(media?.file ?? "playback.mp4")}`}
            controls
            preload="metadata"
            onTimeUpdate={(e) => onTime(e.currentTarget.currentTime)}
            onSeeked={(e) => onTime(e.currentTarget.currentTime)}
            onPlay={() => onPlaying(true)}
            onPause={() => onPlaying(false)}
            onEnded={() => onPlaying(false)}
          />
        ) : (
          <div className="noshot">
            <b>No replay for this recording</b>
            <span>{media?.error ?? "This recording has no screen video or narration audio."}</span>
          </div>
        )}
      </div>

      <div className="shot-caption mono">
        {view === "replay" ? (
          <>
            <span>{hasMedia ? `${media!.video ? "screen video" : "no video"} · ${media!.audio ? "narration audio" : "no audio"}` : "—"}</span>
            <span>{hasMedia && media!.audio && media!.audio_offset_s != null ? `audio placed at ${media!.audio_offset_s.toFixed(3)} s on the recording clock` : ""}</span>
          </>
        ) : obs ? (
          <>
            <span>
              {step ? view : "final"} · {obs.status.replaceAll("_", " ")}
              {obs.t_capture_start !== undefined && ` · captured at ${fmtTime(obs.t_capture_start)}`}
              {step && obs.offset_s !== undefined && ` (${fmtOffset(obs.offset_s)} the action ${view === "after" ? "ended" : "started"})`}
              {obs.source === "video" && " · from video (lossy)"}
            </span>
            <span>{STATUS_TEXT[obs.status] ?? ""}</span>
          </>
        ) : <span>—</span>}
      </div>
      {step && a?.type === "scroll" && view !== "replay" && (
        <div className="scroll-runs mono">
          {scrollRuns(a).map((r, i) => <span key={i}>{r.direction} {+r.amount.toFixed(2)}</span>)}
          {a.events && <span className="faint">{a.events.length} wheel events</span>}
        </div>
      )}
    </section>
  );
});

function Dot({ status }: { status?: string }) {
  const cls = status === "missing" ? "sev high" : status === "ok" || status === "settled" ? "sev ok" : "sev warn";
  return <span className={cls} title={status} style={{ marginLeft: 6 }} />;
}

export default Stage;
