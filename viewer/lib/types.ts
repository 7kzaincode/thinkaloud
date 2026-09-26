export type Severity = "high" | "warn" | "info";

export interface Flag {
  code: string;
  severity: Severity;
  detail: string;
  source: "qc" | "reviewer";
  /** set when a reviewer flag was created by accepting an AI suggestion */
  provenance?: { ai_run_id: string; suggestion: string };
  /** privacy flags: hash of the content the flag is about (dismissals only carry over to the same content) */
  subject?: string;
}

export interface ScrollEvent { t: number; x: number; y: number; dx: number; dy: number; raw?: number }
export interface ScrollRun { direction: "up" | "down" | "left" | "right" | "none"; amount: number; t_start: number; t_end: number; n_events: number }

export type Action =
  | {
      type: "click"; x: number; y: number; button: string; count?: number; mods?: string[]; screen_x?: number; screen_y?: number;
      /** a drag that couldn't become a clean drag step (other input in between, or a double-click drag) */
      release?: { x: number; y: number; t: number }; drag_problem?: string;
    }
  | {
      type: "drag"; x: number; y: number; x2: number; y2: number; button: string; mods?: string[];
      screen_x?: number; screen_y?: number; screen_x2?: number; screen_y2?: number;
    }
  | { type: "type"; text: string; redacted?: boolean; masked_chars?: number; backspaces?: number; keystrokes?: string }
  | { type: "key"; key: string; repeat?: number }
  | {
      type: "scroll"; x: number; y: number; unit?: string; mods?: string[];
      events?: ScrollEvent[]; runs?: ScrollRun[]; net_dx?: number; net_dy?: number;
      /** schema 0.1 only: net amounts */
      dx?: number; dy?: number;
    };

export type BeforeStatus = "ok" | "predates_previous_action" | "stale" | "missing" | "at_action" | "legacy_earlier_action";
export type AfterStatus = "settled" | "unsettled" | "missing";

export interface Observation {
  status: BeforeStatus | AfterStatus | "ok";
  file: string | null;
  source?: "still" | "video";
  capture_seq?: number | null;
  t_capture_start?: number;
  t_capture_end?: number;
  offset_s?: number;
  reason?: string | null;
}

export interface Target {
  status: string;
  role?: string;
  name?: string;
  automation_id?: string;
  class_name?: string;
  framework?: string;
  rect?: number[] | null;
  frame_rect?: number[] | null;
  is_password?: boolean;
  url?: string;
  url_status?: string;
  latency_ms?: number;
  /** found by walking down from a Document hit where overlapping siblings contained the point */
  ambiguous?: boolean;
  hit_method?: "point" | "descend";
  reliable?: boolean;
  error?: string;
}

export interface NarrationRef { segment_id: number; t_start: number; t_end: number; timing: "before_action" | "during_action" | "after_action" }

export interface Step {
  id: number;
  uid?: string;
  t_start: number;
  t_end: number;
  action: Action;
  description?: string;
  context?: { window_title?: string | null; process?: string | null; hwnd?: number | null } | null;
  target?: Target | null;
  observations?: { before: Observation; after: Observation };
  screenshot: string | null;
  reasoning: string;
  reasoning_source: "narrated" | "carried" | "reviewer" | null;
  carried_from: number | null;
  /** what the processor produced, kept when a reviewer edits the reasoning */
  reasoning_original?: { text: string; source: Step["reasoning_source"]; carried_from: number | null };
  transcript_ids: number[];
  narration?: NarrationRef[];
  flags: Flag[];
  dismissed_flags?: Flag[];
  edited?: boolean;
}

export interface Segment {
  id: number;
  t_start: number;
  t_end: number;
  text: string;
  step_id: number | null;
  timing?: NarrationRef["timing"];
}

export interface Media {
  file: string | null;
  status: "ok" | "failed" | "unavailable";
  video?: boolean;
  audio?: boolean;
  audio_offset_s?: number | null;
  audio_clock_drift_ppm?: number | null;
  audio_max_drift_ms?: number;
  video_frames?: number | null;
  video_dropped?: number | null;
  video_first_pts_s?: number | null;
  error?: string | null;
  note?: string;
}

// ---- AI-assisted review (suggestions only; humans decide) -----------------------------
export type Decision = "accepted" | "rejected" | null;

export interface AiRun {
  id: string;
  kind: "narration" | "checklist" | "final_screen";
  at: string;
  /** "anthropic" | "gemini"; absent on runs made before providers were configurable (Anthropic) */
  provider?: string;
  model: string;
  status: "ok" | "error";
  note?: string;
  error?: string;
  error_type?: string;
}

export interface StepAssessment {
  uid: string;
  run_id: string;
  verdict: "explains" | "partially_explains" | "does_not_explain" | "filler" | "missing";
  explanation: string;
  input_hash: string;
  decision: Decision;
  decided_at?: string;
}

export interface ChecklistItem {
  id: string;
  text: string;
  /** human: typed by the reviewer; ai: drafted by the model and accepted (possibly edited) by a human */
  origin: "human" | "ai";
  ai_run_id?: string;
  /** set only by a human */
  human_verdict: "met" | "not_met" | "unclear" | null;
  human_verdict_source?: "reviewer" | "accepted_ai_suggestion";
  /** the AI check a human adopted as their verdict (provenance) */
  accepted_from?: { run_id: string; verdict: "supported" | "contradicted" | "unknown"; input_hash: string };
  /** the item text at the time the verdict was given */
  verdict_text?: string;
  ai_check?: {
    run_id: string;
    verdict: "supported" | "contradicted" | "unknown";
    evidence: string;
    input_hash: string;
  } | null;
}

export interface ChecklistDraft {
  id: string;
  text: string;
  run_id: string;
  decision: Decision;
}

export interface Review {
  outcome: "pass" | "fail" | null;
  reviewer: string | null;
  notes: string;
  edited: boolean;
  reviewed_at?: string;
  /** hash of the processor's trajectory.json this review was made against */
  base_hash?: string;
  rebased?: { at: string; from_hash: string; dropped: string[] };
  rebase_history?: { at: string; from_hash: string; dropped: string[] }[];
  /** recording-level high flags (e.g. password_masking_off) the reviewer checked */
  dismissed_session_flags?: string[];
  checklist?: ChecklistItem[];
  ai?: {
    runs: AiRun[];
    step_assessments: StepAssessment[];
    checklist_drafts: ChecklistDraft[];
  };
}

export interface Trajectory {
  schema_version: string;
  session_id: string;
  task: string;
  success_criteria: string;
  recorded_at: string | null;
  duration_s: number | null;
  source?: { session_schema: string; recorder_version: string | null; legacy: boolean };
  screen: { w: number | null; h: number | null };
  coordinate_space?: { frame_size: (number | null)[]; frame_origin_on_screen: number[]; monitor_dpi_scale: number | null };
  timeline?: { audio_offset_s?: number | null; pauses?: (number | null)[][] };
  media?: Media;
  final_screenshot: string | null;
  final_observation?: Observation;
  steps: Step[];
  transcript: Segment[];
  session_flags: Flag[];
  qc: { summary: string; counts: Record<string, number>; high_severity: number; redacted_steps?: number };
  review: Review;
}

export interface ProcessingStatus {
  state: "queued" | "running" | "done" | "failed" | "interrupted";
  attempts: number;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  job_id?: string | null;
}

export interface SessionSummary {
  id: string;
  task: string;
  recorded_at: string | null;
  duration_s: number | null;
  processed: boolean;
  processing: ProcessingStatus | null;
  schema_version: string | null;
  legacy: boolean;
  metrics: import("./metrics").SessionMetrics | null;
  reviewed: boolean;
  outcome: "pass" | "fail" | null;
  warnings: string[];
  error?: string;
}
