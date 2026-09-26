# Implementation checklist

Status legend: NOT STARTED / IN PROGRESS / IMPLEMENTED (code exists, not yet shown to work) / VERIFIED (acceptance
check ran and passed) / BLOCKED. Evidence ids (C1, C2, …) refer to [VALIDATION_LOG.md](VALIDATION_LOG.md);
test names refer to `processor/tests/` and `viewer/lib/review.test.ts`.

## Phase A: correct recorded data

| ID | Requirement | Implementation | Acceptance evidence | Status |
|---|---|---|---|---|
| A1.1 | Scroll grouping is event-driven, not a 1.5 s timer | `processor/thinkaloud/steps.py` | `test_continuous_scroll_longer_than_old_timer_is_one_step`; native: 4 notches, 1.8 s pause, 4 more, 3 up = 1 step (C20) | VERIFIED |
| A1.2 | Split on other input, window change, configurable long pause, stop/pause | `steps.py` (`SegmentConfig.scroll_pause_s`) | `test_scroll_click_scroll_splits_into_three`, `test_pause_under_threshold…`, `test_window_change_splits_scroll`, `test_modifier_change…`, `test_stop_during_scroll_flushes_final_step`, `test_pause_marker_ends_scroll_step` | VERIFIED |
| A1.3 | Raw scroll events, directions, deltas, units preserved; no misleading net | `steps.py` runs/events; recorder raw wheel delta (`win32_event_filter`) | `test_direction_change_keeps_both_runs_and_raw_events` (fractional delta kept, no bare `dy`); native runs down 8 / up 3 with 11 raw events (C20); pynput floor-division bug documented (reproduction 3) | VERIFIED |
| A1.4 | Screenshot settling separate from step boundaries; settled capture after scrolling; final flush | recorder `capture_loop` settled/end stills | native scroll after-state `settled` (C20); `test_final_step_after_uses_end_capture…` | VERIFIED |
| A2.1 | Before + after with capture timestamps and status | recorder ring + `stills.jsonl`; `observations.py` | ownership tests (9); native pixel check: gray before / green after Add to Cart, blue after navigation (C20); validator enforces ownership (`test_validator_catches…ownership`) | VERIFIED |
| A2.2 | One monotonic timebase, documented mappings | recorder clock, video PTS, audio offset; README "Timeline" | C6, C21 | VERIFIED |
| A2.3 | Rapid actions, delayed render, capture failure, final action | statuses `predates_previous_action`, `unsettled`, `missing`, `stale`; still/video backlog → `dropped` recorded | `test_after_never_borrows…`, `test_unsettled…`, `test_before_that_predates…`, `test_stale_and_missing_before`, synthetic YYZ→Tab after `missing` | VERIFIED |
| A2.4 | Coordinate-space metadata | meta `coordinate_space`; trajectory `coordinate_space`, `frame_rect` | `test_monitor_origin_transform_and_target_kept`; native coordinates match pixels (C20) | VERIFIED |
| A2.5 | Schema versioned; 0.1 recordings still load; missing after stays missing | pipeline legacy path, `legacy_earlier_action`/`at_action`, viewer `normalize()`, review rebase for 0.1 reviews | `test_legacy_v01_recording_still_processes`, `test_legacy_recording_exports…`; user's 3 real 0.1 recordings reprocessed with reviews preserved (C15) | VERIFIED |
| A2.6 | Before/after inspection in viewer | `Stage.tsx` | browser check (C12) | VERIFIED |
| A3.1 | UIA target metadata incl. URL when reliable | `recorder/uia.py` (+ descend from container hits) | native: button "Add to Cart", edit "Search", password edit, link "Go to page 2", `file:///…page.html` URL, 20 ms (C20, C31); packaged app names the button via the descend fallback (C30) | VERIFIED |
| A3.2 | Non-blocking lookups, timeouts, stale-skip, fallback | UIA worker thread, UIA transaction timeout 600 ms, `skipped_stale`, 500 ms reliability cut-off | `test_description_uses_reliable_target_only`; C3 (803 ms lookup on blocked app bounded, not used) | VERIFIED |
| A3.3 | "Clicked the Add to Cart button" with raw metadata | `describe.py` / `format.ts`, StepPanel raw details | native description (C20), browser (C12) | VERIFIED |
| A4.1 | ~4 FPS configurable video, bounded memory, encoder failure, cleanup | `VideoWriter` (bounded queue, drops counted, error state drains), `--fps` | C3/C20 (0 dropped), recorder exit 0 with closed, decodable MKV | VERIFIED |
| A4.2 | Playback with audio; play/pause/seek; step click seeks; active step | `media.py`, media Range route, `Stage.tsx`, `Timeline.tsx` | browser: 72.5 s MP4 playing, playhead, follow mode (C12) | VERIFIED |
| A4.3 | Shared timeline; no accumulating error; ≤ 1 frame on multi-minute run | capture-PTS video, audio offset | 199 s run: 60/60 flashes, 16–273 ms, never early, 0 dropped, 22.7 ppm drift (C21) | VERIFIED (video); audio acoustic latency BLOCKED (C22) |
| A4.4 | Missing audio/video handled | Stage "No replay…" state, legacy audio-only | legacy fixture (media unavailable) renders explanatory state | VERIFIED |

## Phase B: reviewable, useful downstream

| ID | Requirement | Implementation | Acceptance evidence | Status |
|---|---|---|---|---|
| B1.1 | Vendor-neutral versioned dataset export with manifest | `export.py` (`thinkaloud.dataset/1.0`) | `test_export_validates_and_the_bundled_validator_agrees`; system-Python validation (C7) | VERIFIED |
| B1.2 | Claude computer-use mapping to a documented schema version | `computer_toolset_20260801` | mapping tests for every action type; docs checked 2026-09-26 | VERIFIED (offline); live API validation BLOCKED (no key) |
| B1.3 | Narration distinct from edits/AI; timing; missing stays missing | dataset `narration`, `reasoning.original`, `ai_assessment.suggestion_only` | `test_dataset_record_content`, `test_human_decisions_and_ai_suggestions_keep_provenance`, `test_claude_export_structure` | VERIFIED |
| B1.4 | Explicit errors/warnings for unsupported actions | `claude_calls` problems → `errors`, `valid_for_training` | tests for redacted/masked/off-screen/unknown key/rounding | VERIFIED |
| B1.5 | Standalone loader/validator + fixtures | `dataset.py` copied as `thinkaloud_dataset.py` | tamper + ownership violation detection tests; frozen engine export validates | VERIFIED |
| B1.6 | One-click export with accurate reporting | `ExportDialog.tsx`, `/api/export`, download route; privacy gate | browser export (C12, C13); packaged app export + download + independent validation (C30); gate (C29) | VERIFIED |
| B2.1 | Anthropic integration, configurable, key server-side | `ai_review.py`, `/api/sessions/[id]/ai`, `/api/ai/status`, desktop `safeStorage` | 14 fixture tests | IMPLEMENTED; live BLOCKED (no key) |
| B2.2 | Narration assessment, filler/missing | `ai_review.py` narration | fixture tests | VERIFIED (fixtures) |
| B2.3 | Editable checklist drafts | drafts → accept/edit/reject | `review.test.ts` drafts test; browser checklist | VERIFIED (fixtures + UI) |
| B2.4 | Final-screen supported/contradicted/unknown | `ai_review.py` final_screen | fixture tests; outcome never set by AI | VERIFIED (fixtures) |
| B2.5 | Human confirmation, provenance, staleness | `lib/review.ts` | 15 viewer tests (incl. undo after re-run, accepted check then re-run, reworded items); provenance exported (`test_checklist_verdict_provenance_and_rewording_are_exported`) | VERIFIED |
| B2.6 | Structured output validation, errors, disclosure, untrusted data | `ai_review.py`, EndPanel consent | error mapping tests (rate limit, auth, 5xx, timeout, network, refusal, truncation, malformed, missing image/credentials) | VERIFIED (fixtures) |
| B3.1 | Batch view with real metrics | `Batch.tsx`, `metrics.ts` | metric denominator test; browser (C12, C15) | VERIFIED |
| B3.2 | Filters, navigation, batch controls | `Batch.tsx`, `/api/batch` | browser: Process selected on 3 recordings (C15) | VERIFIED |
| B3.3 | Docker batch: concurrency, status, retries, idempotent, restart | `batch.py`, Dockerfile, compose | C17, C18; 5 batch tests | VERIFIED |

## Integration

| ID | Requirement | Status |
|---|---|---|
| I1 | Full journey task → record → process → inspect → replay → AI → decisions → batch → export | VERIFIED except live AI: packaged desktop app in one run, record → process → review → replay → checklist/outcome → export → download → reload → recordings page, 17/17 (C30); batch (C15, C28); AI step BLOCKED (no key; fixture-tested) |
| I2 | Persistence across reloads; reprocessing keeps human edits | VERIFIED (C12 autosave; C15 rebase) |
| I3 | Clear recording state, stop and pause, password masking | VERIFIED (masking by click and by Tab, fail-closed: C31; pause C23) |
| I4 | Independent review A (correctness/data integrity) | VERIFIED: 19 findings, all fixed with tests or checks (VALIDATION_LOG "Review A") |
| I5 | Independent review B (product/adversarial) | VERIFIED: 20 findings, all fixed with tests or checks (VALIDATION_LOG "Review B") |
| I6 | Re-review of the fixes and the final integrated state | IN PROGRESS |
