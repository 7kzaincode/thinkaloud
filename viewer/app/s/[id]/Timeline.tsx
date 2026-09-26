"use client";

import type { Step, Trajectory } from "@/lib/types";
import { fmtTime } from "@/lib/format";

const IDLE = 20;

function worst(s: Step): "high" | "warn" | "info" | null {
  if (s.flags.some((f) => f.severity === "high")) return "high";
  if (s.flags.some((f) => f.severity === "warn")) return "warn";
  if (s.flags.length) return "info";
  return null;
}

export default function Timeline({
  t, sel, onSelect, playhead, activeIdx, onSeek,
}: {
  t: Trajectory; sel: number | "end"; onSelect: (s: number | "end") => void;
  playhead?: number | null; activeIdx?: number | null; onSeek?: (t: number) => void;
}) {
  const dur = Math.max(t.duration_s ?? 0, ...t.steps.map((s) => s.t_end), ...t.transcript.map((g) => g.t_end), 1);
  const pct = (x: number) => `${(x / dur) * 100}%`;
  const selStep = typeof sel === "number" ? t.steps[sel] : null;
  const selSegs = new Set(selStep?.transcript_ids ?? []);
  const gaps: [number, number][] = [];
  t.steps.reduce((prevEnd, s) => {
    if (s.t_start - prevEnd > IDLE) gaps.push([prevEnd, s.t_start]);
    return Math.max(prevEnd, s.t_end);
  }, 0);

  const seekFromEvent = (e: React.MouseEvent<SVGRectElement>) => {
    if (!onSeek) return;
    const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    onSeek(Math.max(0, Math.min(dur, ((e.clientX - box.left) / box.width) * dur)));
  };

  return (
    <div className="tl">
      <svg role="group" aria-label="Session timeline">
        {onSeek && <rect x="0" y="0" width="100%" height="56" fill="transparent" style={{ cursor: "crosshair" }} onClick={seekFromEvent}>
          <title>Click to seek the replay</title>
        </rect>}
        <defs>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--warn)" strokeWidth="1.5" opacity="0.45" />
          </pattern>
        </defs>
        {/* narration row */}
        <line x1="0" x2="100%" y1="9" y2="9" stroke="var(--rule)" />
        {t.transcript.map((g) => (
          <rect key={g.id} x={pct(g.t_start)} width={pct(Math.max(g.t_end - g.t_start, dur * 0.004))} y="4" height="10" rx="2"
            fill={selSegs.has(g.id) ? "var(--accent)" : g.step_id === null ? "var(--faint)" : "var(--muted)"}
            opacity={selSegs.has(g.id) ? 1 : 0.45}>
            <title>{`${fmtTime(g.t_start)}  “${g.text}”`}</title>
          </rect>
        ))}
        {/* idle gaps */}
        {gaps.map(([a, b]) => (
          <rect key={a} x={pct(a)} width={pct(b - a)} y="22" height="26" fill="url(#hatch)">
            <title>{`${(b - a).toFixed(1)}s idle`}</title>
          </rect>
        ))}
        <line x1="0" x2="100%" y1="35" y2="35" stroke="var(--rule)" />
        {/* steps */}
        {t.steps.map((s, i) => {
          const w = worst(s);
          const active = sel === i;
          const playing = activeIdx === i;
          const color = active ? "var(--accent)" : w === "high" ? "var(--high)" : w === "warn" ? "var(--warn)" : "var(--ink)";
          return (
            <g key={s.id} className="tick" onClick={() => onSelect(i)}>
              <rect className="hit" x={pct(Math.max(0, s.t_start - dur * 0.003))} y="18" rx="3"
                width={pct(Math.max(s.t_end - s.t_start, 0) + dur * 0.006)} height="34" fill="transparent" />
              <rect x={pct(s.t_start)} y={active ? 20 : 24} width={pct(Math.max(s.t_end - s.t_start, dur * 0.0025))}
                height={active ? 30 : 22} rx="1.5" fill={color} opacity={w || active ? 1 : 0.7} />
              {w === "high" && !active && <circle cx={pct(s.t_start)} cy="20" r="2.5" fill="var(--high)" />}
              {playing && <rect x={pct(s.t_start)} y="52" width={pct(Math.max(s.t_end - s.t_start, dur * 0.0025))} height="3" fill="var(--ok)" />}
              <title>{`#${s.id} ${fmtTime(s.t_start)} ${s.action.type}${s.flags.length ? " · " + s.flags.map((f) => f.code).join(", ") : ""}`}</title>
            </g>
          );
        })}
        {/* end state */}
        <g className="tick" onClick={() => onSelect("end")}>
          <line x1="100%" x2="100%" y1="18" y2="52" stroke={sel === "end" ? "var(--accent)" : "var(--ink)"} strokeWidth="2" />
          <title>End state</title>
        </g>
        {/* playhead */}
        {playhead != null && (
          <g pointerEvents="none">
            <line x1={pct(playhead)} x2={pct(playhead)} y1="0" y2="56" stroke="var(--ok)" strokeWidth="1.5" />
            <circle cx={pct(playhead)} cy="2" r="3" fill="var(--ok)" />
          </g>
        )}
        {/* axis */}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <text key={f} x={`${f * 100}%`} y="63" fontSize="10" fill="var(--faint)"
            textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"} fontFamily="var(--font-mono)">
            {fmtTime(dur * f)}
          </text>
        ))}
      </svg>
      <div className="tl-legend">
        <span><i style={{ background: "var(--muted)", opacity: 0.45 }} />narration</span>
        <span><i style={{ background: "var(--ink)" }} />step</span>
        {playhead != null && <span><i style={{ background: "var(--ok)" }} />replay position</span>}
        <span><i style={{ background: "var(--warn)" }} />needs attention</span>
        <span><i style={{ background: "var(--high)" }} />privacy</span>
        <span><i style={{ background: "repeating-linear-gradient(45deg, var(--warn) 0 1.5px, transparent 1.5px 5px)", opacity: 0.7 }} />idle &gt; {IDLE}s</span>
      </div>
    </div>
  );
}
