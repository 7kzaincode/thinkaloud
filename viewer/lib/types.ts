export type Severity = "high" | "warn" | "info";

export interface Flag {
  code: string;
  severity: Severity;
  detail: string;
  source: "qc" | "reviewer";
}

export type Action =
  | { type: "click"; x: number; y: number; button: string; count?: number }
  | { type: "type"; text: string; redacted?: boolean }
  | { type: "key"; key: string; repeat?: number }
  | { type: "scroll"; x: number; y: number; dx: number; dy: number };

export interface Step {
  id: number;
  t_start: number;
  t_end: number;
  action: Action;
  screenshot: string | null;
  reasoning: string;
  reasoning_source: "narrated" | "carried" | "reviewer" | null;
  carried_from: number | null;
  transcript_ids: number[];
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
}

export interface Trajectory {
  schema_version: string;
  session_id: string;
  task: string;
  success_criteria: string;
  recorded_at: string | null;
  duration_s: number | null;
  screen: { w: number | null; h: number | null };
  final_screenshot: string | null;
  steps: Step[];
  transcript: Segment[];
  session_flags: Flag[];
  qc: { summary: string; counts: Record<string, number>; high_severity: number; redacted_steps?: number };
  review: {
    outcome: "pass" | "fail" | null;
    reviewer: string | null;
    notes: string;
    edited: boolean;
    reviewed_at?: string;
  };
}

export interface SessionSummary {
  id: string;
  task: string;
  duration_s: number | null;
  steps: number;
  summary: string;
  high: number;
  reviewed: boolean;
  outcome: "pass" | "fail" | null;
  recorded_at: string | null;
}
