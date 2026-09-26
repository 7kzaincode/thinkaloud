import type { Action } from "./types";

export function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${String(m).padStart(2, "0")}:${sec.toFixed(1).padStart(4, "0")}`;
}

export function describe(a: Action): { verb: string; body: string } {
  switch (a.type) {
    case "click":
      return {
        verb: a.count && a.count > 1 ? "double-click" : a.button === "right" ? "right-click" : "click",
        body: `(${a.x}, ${a.y})`,
      };
    case "type":
      return { verb: "type", body: a.redacted ? "" : JSON.stringify(a.text) };
    case "key":
      return { verb: "press", body: a.key + (a.repeat && a.repeat > 1 ? ` ×${a.repeat}` : "") };
    case "scroll":
      return { verb: "scroll", body: `${a.dy < 0 ? "down" : "up"} ${Math.abs(a.dy)}${a.dx ? `, dx ${a.dx}` : ""}` };
  }
}
