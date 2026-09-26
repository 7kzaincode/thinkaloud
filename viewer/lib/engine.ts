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

/**
 * Start a long-running engine command (batch processing). It counts as started once the
 * engine prints its {"event": "started"} line; exiting before that (engine missing, bad
 * arguments, no recordings found) is reported as a failure to start. A job that starts and
 * then finishes quickly with a failed recording is a started job, not a start failure.
 */
export function startEngine(args: string[], settleMs = 15_000): Promise<{ pid: number } | { error: string }> {
  const [cmd, base] = engineCommand();
  return new Promise((resolve) => {
    let out = "";
    let done = false;
    const child = spawn(cmd, [...base, ...args], {
      windowsHide: true, detached: false, stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
    });
    let started = false;
    const finish = (r: { pid: number } | { error: string }) => { if (!done) { done = true; resolve(r); } };
    const collect = (d: Buffer) => {
      out = (out + d).slice(-4000);
      if (!started && /"event":\s*"started"/.test(out)) {
        started = true;
        finish({ pid: child.pid ?? 0 });
      }
    };
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);
    child.on("error", (e) => finish({ error: `could not start the engine: ${e.message}` }));
    child.on("exit", (code) => {
      if (started) return;
      const lines = out.trim().split(/\r?\n/);
      const last = lines.reverse().find((l) => l.includes("error")) ?? lines[0];
      finish(code === 0 ? { pid: child.pid ?? 0 } : { error: `engine exited (${code}): ${last ?? ""}`.slice(0, 400) });
    });
    setTimeout(() => finish({ pid: child.pid ?? 0 }), settleMs); // still running, just quiet
  });
}
