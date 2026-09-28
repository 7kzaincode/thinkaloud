import "server-only";
import { randomBytes } from "crypto";
import { promises as fs } from "fs";
import os from "os";
import path from "path";
import { DATA_DIR } from "./sessions.ts";

/**
 * Speech-to-text models the app offers (faster-whisper, run locally by the engine).
 * Same table as processor/thinkaloud/transcribe.py SPEECH_MODELS.
 */
export const SPEECH_MODELS = [
  { id: "base.en", label: "Fast", download_mb: 145, repo: "Systran/faster-whisper-base.en",
    note: "Quickest, but mishears a lot, especially with background noise." },
  { id: "small.en", label: "Balanced", download_mb: 465, repo: "Systran/faster-whisper-small.en",
    note: "Noticeably more accurate; about 2–3× slower than Fast." },
  { id: "large-v3-turbo", label: "Accurate", download_mb: 1620, repo: "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    note: "Most accurate; about 5× slower than Fast (still a fraction of the recording's length on most PCs)." },
] as const;
export type SpeechModelId = (typeof SPEECH_MODELS)[number]["id"];
export const DEFAULT_SPEECH_MODEL: SpeechModelId = "large-v3-turbo";

const SETTINGS = () => path.join(/*turbopackIgnore: true*/ DATA_DIR, "settings.json");

async function readSettings(): Promise<Record<string, unknown>> {
  try {
    const j = JSON.parse(await fs.readFile(SETTINGS(), "utf-8"));
    return j && typeof j === "object" ? j : {};
  } catch {
    return {};
  }
}

/** The model processing uses: THINKALOUD_WHISPER_MODEL (set by an admin) > Settings > default. */
export async function speechModel(): Promise<{ id: string; source: "environment" | "settings" | "default" }> {
  const env = process.env.THINKALOUD_WHISPER_MODEL;
  if (env) return { id: env, source: "environment" };
  const chosen = (await readSettings()).speech_model;
  if (typeof chosen === "string" && SPEECH_MODELS.some((m) => m.id === chosen)) return { id: chosen, source: "settings" };
  return { id: DEFAULT_SPEECH_MODEL, source: "default" };
}

export async function setSpeechModel(id: string): Promise<void> {
  if (!SPEECH_MODELS.some((m) => m.id === id)) throw new Error("unknown speech model");
  const next = { ...(await readSettings()), speech_model: id };
  await fs.mkdir(DATA_DIR, { recursive: true });
  const tmp = `${SETTINGS()}.${randomBytes(4).toString("hex")}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(next, null, 2), "utf-8");
  await fs.rename(tmp, SETTINGS());
}

/** Where huggingface_hub keeps downloaded models (the desktop app points HF_HOME into its data folder). */
function hubCache(): string {
  if (process.env.HF_HUB_CACHE) return process.env.HF_HUB_CACHE;
  const home = process.env.HF_HOME ?? path.join(/*turbopackIgnore: true*/ os.homedir(), ".cache", "huggingface");
  return path.join(/*turbopackIgnore: true*/ home, "hub");
}

export async function modelDownloaded(repo: string): Promise<boolean> {
  const snaps = path.join(/*turbopackIgnore: true*/ hubCache(), `models--${repo.replace("/", "--")}`, "snapshots");
  for (const s of await fs.readdir(snaps).catch(() => [] as string[])) {
    if (await fs.access(path.join(/*turbopackIgnore: true*/ snaps, s, "model.bin")).then(() => true, () => false)) return true;
  }
  return false;
}
