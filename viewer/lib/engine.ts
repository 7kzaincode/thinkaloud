import "server-only";
import { spawn } from "child_process";
import { existsSync } from "fs";
import path from "path";

/**
 * How the viewer server runs the Python engine (exports, AI review, batch processing).
 * THINKALOUD_ENGINE: JSON array command set by the desktop app (frozen engine .exe);
 * otherwise the repo's .venv + engine/engine.py (development).
 */
export function engineCommand(): [string, string[]] {
  const env = process.env.THINKALOUD_ENGINE;
  if (env) {
    const arr = JSON.parse(env) as string[];
    return [arr[0], arr.slice(1)];
  }
  const repo = path.resolve(/*turbopackIgnore: true*/ process.cwd(), "..");
  const win = process.platform === "win32";
  const py = path.join(/*turbopackIgnore: true*/ repo, ".venv", win ? "Scripts" : "bin", win ? "python.exe" : "python");
  return [existsSync(py) ? py : "python", [path.join(/*turbopackIgnore: true*/ repo, "engine", "engine.py")]];
}

export interface EngineResult {
  code: number | null;
  stdout: string;
  stderr: string;
  json: unknown;
}

/** Run one engine command, feed `input` on stdin, parse the LAST JSON line of stdout. */
export function runEngine(args: string[], input?: unknown, timeoutMs = 180_000): Promise<EngineResult> {
  const [cmd, base] = engineCommand();
  return new Promise((resolve) => {
    const child = spawn(cmd, [...base, ...args], {
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr = (stderr + d).slice(-8000)));
    child.on("error", (e) => {
      clearTimeout(timer);
      resolve({ code: -1, stdout, stderr: String(e), json: null });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      let json: unknown = null;
      const lines = stdout.trim().split(/\r?\n/).reverse();
      for (const l of lines) {
        try {
          json = JSON.parse(l);
          break;
        } catch {
          /* not JSON */
        }
      }
      resolve({ code, stdout, stderr, json });
    });
    if (input !== undefined) child.stdin.end(JSON.stringify(input));
    else child.stdin.end();
  });
}

/** Start a long-running engine command without waiting (batch processing). */
export function startEngine(args: string[]): number | undefined {
  const [cmd, base] = engineCommand();
  const child = spawn(cmd, [...base, ...args], {
    windowsHide: true, detached: false, stdio: "ignore",
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
  });
  child.unref();
  return child.pid;
}
