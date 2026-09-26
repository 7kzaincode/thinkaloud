import type { Action, Step } from "./types.ts";

export function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${String(m).padStart(2, "0")}:${sec.toFixed(1).padStart(4, "0")}`;
}

export function fmtOffset(s: number | undefined): string {
  if (s === undefined || s === null) return "";
  const ms = Math.round(s * 1000);
  return ms === 0 ? "at the same moment" : ms < 0 ? `${-ms} ms before` : `${ms} ms after`;
}

/** Mirrors processor/thinkaloud/describe.py (TARGET_MAX_LATENCY_MS = 500). */
const ROLE_WORDS: Record<string, string> = {
  edit: "field", combobox: "dropdown", "list item": "item", "tab item": "tab", "data item": "row",
  "tree item": "item", "split button": "button",
};
const KEY_LABELS: Record<string, string> = {
  enter: "Enter", tab: "Tab", esc: "Esc", backspace: "Backspace", delete: "Delete", up: "Up", down: "Down",
  left: "Left", right: "Right", page_up: "Page Up", page_down: "Page Down", home: "Home", end: "End",
  space: "Space", ctrl: "Ctrl", alt: "Alt", shift: "Shift", cmd: "Win",
};

export function keyLabel(combo: string): string {
  // "ctrl++" is Ctrl and the + key, not Ctrl and two empty keys
  let parts = combo.split("+");
  if (combo === "+" || combo.endsWith("++")) {
    const head = combo.slice(0, -1).replace(/\++$/, "");
    parts = [...(head ? head.split("+") : []), "+"];
  }
  return parts.map((p) => KEY_LABELS[p] ?? (p.length === 1 ? p.toUpperCase() : p.replace(/_/g, " "))).join("+");
}

export function targetReliable(step: Pick<Step, "target">): boolean {
  const t = step.target;
  return !!t && t.status === "ok" && !t.ambiguous && (t.latency_ms ?? 0) <= 500;
}

export function scrollRuns(a: Extract<Action, { type: "scroll" }>) {
  if (a.runs) return a.runs.filter((r) => r.direction !== "none");
  // schema 0.1: only a net amount survives
  const dy = a.dy ?? 0, dx = a.dx ?? 0;
  if (dy) return [{ direction: dy < 0 ? "down" : "up", amount: Math.abs(dy), t_start: 0, t_end: 0, n_events: 0 } as const];
  if (dx) return [{ direction: dx < 0 ? "left" : "right", amount: Math.abs(dx), t_start: 0, t_end: 0, n_events: 0 } as const];
  return [];
}

export function describe(step: Pick<Step, "action" | "target" | "description">): string {
  if (step.description) return step.description;
  const a = step.action;
  switch (a.type) {
    case "click": {
      if (a.drag_problem && a.release) {
        const start = (a.count ?? 1) > 1 ? "Double-clicked and dragged" : "Dragged";
        return `${start} from (${a.x}, ${a.y}) to (${a.release.x}, ${a.release.y}) (${a.drag_problem})`;
      }
      let verb = a.count === 2 ? "Double-clicked" : a.count === 3 ? "Triple-clicked" : "Clicked";
      if (a.button === "right") verb = "Right-clicked";
      if (a.button === "middle") verb = "Middle-clicked";
      if (targetReliable(step)) {
        const name = (step.target!.name ?? "").trim();
        const role = ROLE_WORDS[step.target!.role ?? ""] ?? step.target!.role ?? "element";
        if (name) return `${verb} the ${name.slice(0, 80)} ${role}`;
      }
      return `${verb} at (${a.x}, ${a.y})`;
    }
    case "drag": {
      const verb = a.button === "right" ? "Right-dragged" : "Dragged";
      const name = targetReliable(step) ? (step.target!.name ?? "").trim() : "";
      if (name) {
        const role = ROLE_WORDS[step.target!.role ?? ""] ?? step.target!.role ?? "element";
        return `${verb} the ${name.slice(0, 80)} ${role} to (${a.x2}, ${a.y2})`;
      }
      return `${verb} from (${a.x}, ${a.y}) to (${a.x2}, ${a.y2})`;
    }
    case "type":
      if (a.redacted) return "Typed text (redacted)";
      if (a.masked_chars && a.masked_chars === a.text.length) return `Typed ${a.masked_chars} characters into a password field (masked)`;
      if (!a.text && a.backspaces) {
        // never quote deleted text: it is often a mistyped password or address
        const n = (a.keystrokes ?? "").replaceAll("⌫", "").length || a.backspaces;
        return `Typed ${n} character${n === 1 ? "" : "s"} and deleted ${n === 1 ? "it" : "them"}`;
      }
      return `Typed "${a.text}"`;
    case "key":
      return `Pressed ${keyLabel(a.key)}${a.repeat && a.repeat > 1 ? ` ×${a.repeat}` : ""}`;
    case "scroll": {
      const runs = scrollRuns(a);
      return runs.length ? "Scrolled " + runs.map((r) => `${r.direction} ${+r.amount.toFixed(2)}`).join(", then ") : "Scrolled";
    }
  }
}

export const STATUS_TEXT: Record<string, string> = {
  ok: "captured before the action",
  at_action: "captured as the action happened (older recorder); may show early effects",
  predates_previous_action: "captured before the previous action finished; may not show its result",
  legacy_earlier_action: "older recorder: screenshot from an earlier action; may not show its result",
  stale: "captured well before the action",
  missing: "not captured",
  settled: "screen had settled after the action",
  unsettled: "captured soon after the action; may still be changing",
};
