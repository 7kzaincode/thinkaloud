// Bridge exposed by desktop/preload.js. Undefined when the viewer runs in a normal browser.
export interface Device {
  index: number;
  name: string;
  default: boolean;
}

export interface EngineEvent {
  event: string;
  [key: string]: unknown;
}

export type AiProvider = "anthropic" | "gemini";

export interface ApiKeyStatus {
  provider: AiProvider | null;
  encryption: boolean;
  keys: Record<AiProvider, { stored: boolean; fromEnvironment: boolean }>;
}

export interface DesktopBridge {
  devices(): Promise<Device[]>;
  startMeter(device: number | null): Promise<void>;
  stopMeter(): Promise<void>;
  startRecording(opts: { task: string; criteria: string; device: number | null }): Promise<boolean>;
  stopRecording(): Promise<void>;
  pauseRecording(paused: boolean): Promise<void>;
  apiKeyStatus(): Promise<ApiKeyStatus>;
  setApiKey(provider: AiProvider, key: string): Promise<boolean>;
  clearApiKey(provider: AiProvider): Promise<boolean>;
  /** null = automatic (Anthropic if it has a key, else Gemini) */
  setAiProvider(provider: AiProvider | null): Promise<boolean>;
  openSessionsFolder(): Promise<void>;
  /** open one recording's folder in File Explorer */
  showRecording?(id: string): Promise<void>;
  info(): Promise<{ sessions: string; dev: boolean; version: string }>;
  onMeter(cb: (e: EngineEvent) => void): () => void;
  onRecorder(cb: (e: EngineEvent) => void): () => void;
  onProcess(cb: (e: EngineEvent) => void): () => void;
}

declare global {
  interface Window {
    thinkaloud?: DesktopBridge;
  }
}

export const desktop = (): DesktopBridge | undefined =>
  typeof window === "undefined" ? undefined : window.thinkaloud;
