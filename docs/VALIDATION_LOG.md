# Validation and review log

Newest entries at the bottom of each section. Commands are run from the repo root unless noted.

## Environment (2026-09-26)

- Windows 11 Pro 10.0.26200, 2560×1440 primary display, Python 3.12.10 (`.venv`), Node 22.14.0, Docker 29.1.3 (Docker Desktop, Linux engine).
- PyAV 18.1.0 (installed with faster-whisper) exposes encoders: `libx264`, `h264_mf`, `aac`, `libopus`, `libvpx-vp9`, `mpeg4`, `mjpeg`, ... (checked with `av.codec.Codec(name, "w")`).
- Audio inputs: MME/DirectSound/WASAPI for Razer Barracuda X (default), Iriun, Voicemod; WDM-KS "Stereo Mix (Realtek)".
- Anthropic credentials: `ANTHROPIC_API_KEY` **not set**, no `ant` CLI profile. Live AI calls are **BLOCKED**.
- The home-folder AGENTS.md asks for gstack's `/browse` skill; it is not installed in this session, so UI checks use the built-in browser pane / Electron capture hook.

## Baseline (before this work)

| Check | Command | Result |
|---|---|---|
| Processor unit tests | `cd processor && ../.venv/Scripts/python -m pytest -q` | 17 passed |
| Viewer typecheck | `cd viewer && npx tsc --noEmit` | pass |
| Viewer build | `cd viewer && npx next build` | pass |
| Desktop syntax | `node --check desktop/*.js` | pass |

Pre-existing failures: none.

## Reproductions of reported problems

1. **"Scroll becomes like 15 actions."** Ran `merge_events` on the user's sessions `2026-09-25T23-37-42` (18 raw scroll events) and `2026-09-25T23-40-01` (15 raw scroll events). Result: 2 and 1 scroll *steps*. The raw events were not split into 15 steps in these recordings because all inter-scroll gaps were < 1.5 s (max 1.0 s). The pill's "N events" counter counts raw events, which is likely the source of "15". However the timer rule would split any scroll with a ≥ 1.5 s pause (common when reading), so the fixed timer is still wrong.
2. **Direction changes lost.** Session `23-40-01` mixes `dy=-1` and `dy=+1` in one scroll step; the step stores only the net `dy`. Confirmed data loss.
3. **Wheel delta truncation in pynput (new finding).** `pynput/mouse/_win32.py:251` computes `SHORT(mouseData >> 16) // WHEEL_DELTA` (floor division). A touchpad delta of +30 becomes 0, −30 becomes −1. Direction-asymmetric data loss. Fix: read raw `mouseData` via `win32_event_filter`.
4. **Before-screenshot labeling.** Recorder v0.1 queues the click screenshot from the hook callback and grabs it asynchronously; the image can include early effects of the click, yet the viewer labels it "screen as the expert saw it before acting". Needs capture timestamps and a frame captured strictly before the action.

## Checks run during this work

| # | Check | Command / method | Result |
|---|---|---|---|
| C1 | UI Automation lookups on this machine | `recorder/uia.py` worker against the taskbar | first run failed: "Cannot change thread mode" (comtypes had initialized COM as STA); fixed with `sys.coinit_flags = 0`; then `button "ChatGPT - 1 running window"` in 9 ms; tree walk hit a NULL COM pointer at the root and reported `error` instead of `no_document`; fixed (`if not ptr`) |
| C2 | Encoder / capture cost at 2560×1440 | benchmark script (grab, libx264 veryfast, PNG level 1/3) | grab 36 ms, x264 ≈4 ms/frame amortised, PNG ≈40 ms |
| C3 | Recorder 0.2 live smoke run (Tk window driven by pynput) | `scripts/smoke_record.py` | events, 6 stills with capture times, 19 video frames, audio offset 0.216 s, drift 27.6 ppm; one UIA lookup took 803 ms because the Tk test window was blocked by its own driver → descriptions distrust targets > 500 ms |
| C4 | MP4 remux of the screen video | `build_playback` | **bug**: first video PTS 0.666 s instead of 0.125 s: x264 B-frames + Matroska dts=None packets were skipped. Fixed: `bf=0` in the recorder, remux keeps packets with pts and sets dts=pts, re-encode fallback when stream copy fails; verified 18/18 frames, first PTS 0.145 s |
| C5 | Processor unit/integration tests | `cd processor && ../.venv/Scripts/python -m pytest -q` | 80 passed (segmentation 18, ownership 9, pipeline/AV 14, export 17, AI boundary 14, batch 5, …) |
| C6 | A/V placement in playback.mp4 | `test_playback_places_audio_on_recording_timeline` (flash + beep at t=2.0 s, audio offset 0.137 s) | first bright frame at 2.115 s (= capture midpoint), beep onset within 70 ms tolerance (AAC granularity) |
| C7 | Export + independent validation | `engine export samples/synthetic-flight --out …` then `python thinkaloud_dataset.py .` with the **system** Python (not the venv) | `OK: 1 recording(s), 0 error(s), 0 warning(s)`; Claude file marked `valid_for_training: false` with 2 explicit errors (QC-redacted email, masked password) |
| C8 | AI boundary tests | fake client (no network) | **bug**: `build_messages` stripped hashes from the shared request via a shallow copy → KeyError; fixed with a deep copy |
| C9 | Batch tests | valid ×2 + legacy + broken recording, concurrency 2, retries 1 | **bug**: queuing overwrote `state`, so reruns were never skipped; fixed with `done_input_hash`; failure isolated, retries, idempotent reruns, input-change reprocessing, crash recovery, live-job respect all pass |
| C10 | Legacy 0.1 screenshots in exports | export of `tests/fixtures/legacy-v01` | validator rejected "predates_previous_action" without a capture end time; legacy captures now use the explicit status `legacy_earlier_action` |
| C11 | Viewer unit tests | `cd viewer && npm test` | 10 passed (hash parity with Python, AI never auto-applies, staleness, drafts, reasoning revert, rebase incl. 0.1 reviews, metric denominators) |
| C12 | Viewer in a browser (user's running dev server, port 54275) | browser pane | batch view metrics; before/after tabs with capture captions; replay: `playback.mp4` served with Range, duration 72.5 s, readyState 4, playhead + follow mode; checklist + outcome autosave; export dialog produced a validated zip |
| C13 | Export dialog wording | browser | headline said "validation errors" while the bundle validated; now separates bundle validation from actions that can't be represented |
| C14 | Privacy: redacted email visible in a screenshot | browser, synthetic step #03 | typed-text redaction does not cover screenshots; added high-severity QC flag `redacted_value_on_screen` |
| C15 | Batch UI on the user's 3 real 0.1 recordings | browser → Process selected | job `b20260926044432-dc57` done 3/0; `23-16-01` 12 → 9 steps (event-driven scroll grouping). **bug**: rebase dropped 2 of the user's edits (0.1 steps have no uid). Fixed (fallback match by start time + action type), original reviews restored from backup and re-rebased: reasoning edit + reviewer flag + both outcomes preserved, 0 dropped |
| C16 | Repository completeness | `git check-ignore` | **bug (pre-existing)**: `.gitignore` `sessions/` matched `viewer/app/api/sessions/`, so the session API routes were never committed; anchored to `/sessions/` etc. and committed |
| C17 | Docker batch on a fixture library (2 valid 0.2, 1 needing Whisper, 1 legacy, 1 corrupt events.jsonl, 1 missing meta.json) | `docker run … thinkaloud-processor batch /data/sessions --jobs /data/jobs --concurrency 2 --retries 1` | 4 done / 2 failed in 11 s; Whisper ran in the container (11 segments of the TTS narration); failures isolated and retried once. Error text was a traceback fragment → processor now raises `InputError` ("events.jsonl line 1 is not valid JSON…", "meta.json is missing…") and the batch extracts it |
| C18 | Docker idempotency and interruption | second run; then `docker kill` mid-run, wait 32 s, run again | second run skipped the 4 up-to-date recordings; after the kill the job and one recording were left `running` with a stale heartbeat; the next run marked the dead job `interrupted`, finished the rest; previous good outputs were intact (atomic writes) |
| C19 | **Incident during the first native E2E run** | `scripts/e2e_capture.py` | the test window opened behind the Claude desktop app, so injected clicks/keys landed there: the search text was sent as a chat message and the dummy password was typed into the chat box. The captured session (screenshots of the user's screen) was deleted immediately. Fix: test window always-on-top; driver activates it only if it is the window under the cursor, then checks foreground + window-under-cursor before **every** click, scroll and keystroke, aborts and deletes the partial session otherwise; sessions deleted after checking unless `--keep` |
| C20 | Native E2E on the Chromium test bench (guarded) | `python scripts/e2e_capture.py --flash-seconds 8` | 25/25 checks pass: UIA button/edit/password edit/link + file:// URL (20 ms lookup), password masked (14 chars, not in any file or reassembled keystrokes), scroll 4+pause 1.8 s+4 down+3 up = 1 step with runs down 8/up 3 and 11 raw events, before image gray swatch / after green (pixel check), page-2 after-state blue, playback.mp4 PTS = capture PTS. Report: `docs/evidence/e2e_report.json` |
| C21 | Multi-minute A/V sync | `python scripts/e2e_capture.py --flash-seconds 180 --flash-interval 3` | 199 s recording, 60 flashes: 60/60 found, 0 shown in a frame captured before the click, event→first frame showing it 16–273 ms (≤ one 250 ms frame + render), 0 dropped frames, audio clock drift 22.7 ppm (≈4.5 ms over the run). Report: `docs/evidence/e2e_sync_report.json` |
| C23 | Pause/resume, native | recorder `--json`, stdin pause at ~0.8 s, resume at ~2.9 s, stop | markers pause/resume in events and `meta.input.pauses`; 0 of 11 video frames captured inside the pause; 0 of 29 345 audio samples non-zero inside the pause (mic noise present outside); session deleted afterwards |
| C24 | Frozen engine (PyInstaller) | `engine/dist/thinkaloud-engine.exe` ai-status, process, export, validate, batch (frozen subprocesses), 3 s record | all succeed; UIA available in the frozen build; export bundle validates; `dataset.py` copied from beside the frozen module (fixed: `ds.__file__` is a .pyc path when frozen) |
| C22 | Acoustic audio alignment on this machine | Stereo Mix (WDM-KS) at 16/48 kHz | **BLOCKED**: PortAudio host error; output is a USB headset, so no loopback or acoustic path to measure mic latency against a known beep. Covered instead by: synthetic A/V placement test (C6) and measured clock drift (C21) |
| C25 | Unit suites after the review fixes | `pytest -q` in processor/ and recorder/; `npm test`, `npx tsc --noEmit` in viewer/ | processor 100 passed, recorder 7 passed (new: fake-UIA recorder tests), viewer 15 passed, typecheck clean |
| C26 | Media Range and API guard against the standalone build | `node .next/standalone/server.js` on 127.0.0.1:3299 with `THINKALOUD_DIRS=samples`, curl | no Range → 200 full; `bytes=0-99` → 206 (100 B); `bytes=100-`, `bytes=-50` → 206; multi-range, reversed (`500-100`), other unit (`items=`), `bytes=-` → 200 full (header ignored per RFC 9110); `bytes=-0` and a start at EOF → 416 with `Content-Range: bytes */size`; end past EOF clamped. Guard: foreign Host → 403, cross-origin PUT → 403, `text/plain` PUT → 415, encoded `../` frame path → 404 |
| C27 | Batch start failures are reported | standalone viewer with `THINKALOUD_ENGINE` pointing at a missing exe, `POST /api/batch` | HTTP 500 `could not start the engine: spawn … ENOENT` (was: "Started job…" and nothing) |
| C28 | Docker batch after the fixes | `docker compose build`; `docker run … batch /data/sessions --concurrency 2 --retries 1` on 3 synthetic copies (one needing Whisper), the 0.1 fixture, and a corrupt `events.jsonl` | 4 done / 1 failed in 22.6 s, failure isolated with the clean `InputError` message and retried once; second run skipped the 4 up-to-date recordings; no `processing.lock` left behind. (From Git Bash, container paths need `MSYS_NO_PATHCONV=1`; without it the first attempt reported "no recordings found".) |
| C29 | Frozen engine export gate | `engine/dist/thinkaloud-engine/thinkaloud-engine.exe export samples/synthetic-flight` then `--allow-privacy-flags`, `validate`, and the bundled `thinkaloud_dataset.py` | gated run: `ok: false`, recording skipped with its 2 open privacy flags listed; override: bundle written, `validate` ok, standalone validator `OK: 1 recording(s), 0 error(s)` |
| C30 | Packaged desktop journey after the fixes | `electron-builder --win --dir`, `node scripts/app_journey.mjs` | first attempt: the guarded driver **aborted before any input** because the user's own app window covered the test bench. Cause: the bench called `setAlwaysOnTop` before the window was shown and was not topmost (`WS_EX_TOPMOST` unset). Fixed: create it topmost, re-assert after show, report the state in layout.json, refuse to drive input if it is not topmost. Re-run **17/17**; the UIA descend fallback now names the click "Clicked the Add to Cart button" (previously "…document"). Report: `docs/evidence/app_journey_report.json` |
| C31 | Native E2E after the fixes, with Tab into a password field | `python scripts/e2e_capture.py --report docs/evidence/e2e_report.json` | **27/27**: new checks: Tab is its own step; the password typed after Tab (no click, different length from the first password) is masked by the focus check; neither password appears in any file or in reassembled keystrokes. Sync: 6/6 flashes, 198–217 ms, none early, 0 dropped |

## Review findings

Two independent fresh-context reviews ran against commit 3dd4e89: **A** (correctness and data integrity) and
**B** (product, integration, adversarial). Every finding was validated (most were reproduced by the reviewer with
a probe on synthetic data), fixed at the root, and covered by a regression test or a reproducible check.
Fixes are in a57bb02 and later. Test names are in `processor/tests/`, `recorder/tests/` and `viewer/lib/review.test.ts`.

### Review A: correctness and data integrity

| # | Sev | Finding | Fix | Evidence |
|---|---|---|---|---|
| A1 | critical | Password typed right after Tab saved in plain text (focus only re-checked at a new burst or click) | the focused field is re-checked after any non-text key; masking fails closed when focus is unknown | `test_password_after_tab_is_masked`, `test_unknown_focus_fails_closed`; native C31 |
| A2 | high | Backspace-only edits dropped; the previous step's after-image then showed the deletion | a Backspace with no typed text becomes a `key backspace` step; typed-then-deleted steps are kept with `keystrokes` | `test_backspace_deleting_existing_text_is_its_own_step`, `test_corrections_keep_raw_keystrokes_and_typed_then_deleted_is_kept` |
| A3 | high | Ctrl+Shift+letter recorded as Ctrl+letter | `key_name` reports the key kind; Shift is kept whenever the key is not a plain character or other modifiers are held | `test_ctrl_shift_letter_keeps_shift_and_altgr_is_text` |
| A4 | medium | `os.replace` fails on Windows while the viewer has the file open; heartbeat thread could die | `fsutil.replace_retry`/`write_json_atomic` everywhere; heartbeat and status writes never raise (errors recorded in the job file) | `test_atomic_write_waits_for_a_reader_to_let_go` |
| A5 | medium | Queued recordings shown as "interrupted" after 30 s | heartbeat refreshes queued and running entries | `test_queued_recordings_keep_heart_beating` |
| A6 | medium | Drags recorded as clicks | recorder records the release when moved ≥ 5 px; `drag` step; Claude `left_click_drag`; viewer shows the path | `test_drag_release_recorded_only_when_moved`, `test_drag_from_press_and_release`, `test_drag_and_punctuation_keys`, `test_description_of_drags_and_deleted_typing` |
| A7 | medium | Backspaces deleting existing text exported as plain typing | see A2: exported as `key BackSpace` with repeat before the typing | `test_type_text_redacted_masked_backspace` |
| A8 | medium | AltGr characters became key steps (email redaction missed, validator/export disagreed on key names) | Ctrl+Alt+printable is text (`altgr`); punctuation mapped to keysym names; export checks keys with the validator's pattern | `test_ctrl_shift_letter_keeps_shift_and_altgr_is_text`, `test_drag_and_punctuation_keys`; real non-US layout not tested (README) |
| A9 | medium | Window context looked up late (seconds after the event) | foreground window handle captured in the input hook, described in the writer | `test_window_captured_at_input_time` |
| A10 | medium | A save conflict (409) lost unsaved review edits | server rebases the incoming draft onto the new trajectory and returns it; the client adopts it | `rebase carries a cleared reasoning and a save-time rebase is not recorded` (viewer); `saveReview` |
| A11 | medium | Schema 0.1 trajectories exported with invalid tool ids and all-missing before images | `normalize()` in `effective_trajectory`; tool ids sanitized | `test_legacy_recording_exports_with_explicit_statuses` |
| A12 | low | Lost tail at stop went unnoticed; audio overflows and recorder errors not surfaced | UIA wait cut to 0.2 s once stopping; QC `recording_incomplete`, `recorder_errors`, `audio_gaps` | `test_session_flags_for_incomplete_recordings_and_capture_problems` |
| A13 | low | Lone Win/Alt never recorded | emitted on release when no other key was pressed | `test_lone_win_key_is_recorded` |
| A14 | low | Rebase silently undid a cleared reasoning | carried whenever `reasoning_original` exists | `rebase carries a cleared reasoning…` (viewer) |
| A15 | low | 0.25 notch exported as 1 notch with only a warning; zero-delta scroll omitted silently | under half a notch is an error; half-up rounding; no-direction scroll is an error | `test_scroll_runs_keep_order_direction_and_warn_on_rounding` |
| A16 | low | Audio: overflows not padded; pause muting decided late; offset estimate unconfirmed | overflow gaps padded with silence (`overflow_padding_s`); pause decided in the callback | offset: acoustic check BLOCKED (C22) |
| A17 | low | Double-click merged across a pause marker and with a ±6 px box | pause/resume/end markers are barriers; box from the recorded system metrics (default ±2 px) | `test_no_double_click_across_pause_and_system_rectangle`, `test_segmentation_uses_the_recorded_double_click_box` |
| A18 | low | Viewer temp file names not unique per write | random temp names (viewer and processor) | code review; C12-style autosave in C30 |
| A19 | low | Stop could hang on a full queue; still-index write unprotected | `put(None, timeout=20)`; index writes guarded | recorder exits 0 in C31 |

### Review B: product, integration, adversarial

| # | Sev | Finding | Fix | Evidence |
|---|---|---|---|---|
| B1 | high | Export trusted the browser-written review file: arbitrary file read into bundles and writes outside the export folder | server merges only review-owned fields; exporter uses the folder name as id and confines images to `frames/*.png` inside the recording | `test_reviewer_file_cannot_inject_paths_or_ids` |
| B2 | high | Browser-mode viewer listened on all interfaces with no Origin check; `text/plain` POSTs bypassed CORS | `-H 127.0.0.1`; `guard()` on every route: loopback Host, same-origin, JSON writes | C26 |
| B3 | high | Password after Tab in plain text; UIA timeout failed open | same as A1 | as A1 |
| B4 | medium | Export ignored open high-severity privacy flags; URLs and titles not scanned | export skips such recordings unless overridden; QC `email_in_context`, `sensitive_url` (redacted/stripped) | `test_open_privacy_flags_block_export_unless_confirmed`, `test_emails_and_tokens_in_urls_titles_are_flagged_and_redacted`; C29 |
| B5 | medium | Queued rows "interrupted"; "Process all new" resubmitted in-flight rows; no claim lock | queued heartbeats; in-flight rows excluded; `O_EXCL` `processing.lock` with stale takeover | `test_stale_lock_is_taken_over_and_locks_are_released`, `test_live_run_by_another_job_is_not_touched` |
| B6 | medium | Export dialog listed skipped recordings as "not representable" under a success headline | `skipped` list; "N of M recordings exported" | `test_stale_review_is_refused`, `test_unprocessed_recording_is_reported` |
| B7 | medium | Autosave could reorder, drop on navigation, collide on temp names, drop edits on 409; export didn't flush | one PUT in flight; flush on unmount and before export; `beforeunload` warning; unique temps; server-side rebase | C30 (autosave, reload persistence) |
| B8 | medium | Undo after an AI re-run left the earlier run's flag | provenance matched by suggestion key | `undo after an AI re-run removes the flag the earlier run created` |
| B9 | medium | Accepted AI verdict lost its provenance; "Use this as my verdict" hidden when the AI later disagreed; rewording kept the verdict | `accepted_from`, `verdict_text`; button shown whenever verdicts differ; outdated-verdict notice; both exported | `a re-run never changes an accepted verdict…`, `test_checklist_verdict_provenance_and_rewording_are_exported` |
| B10 | low–medium | Export name collisions (same id, same second) | recordings keyed by folder name; random bundle suffix | `test_bundle_names_never_collide` |
| B11 | low | Corrupt screenshot and route timeout surfaced as a generic engine error | typed `bad_image`; route timeout 900 s > SDK worst case | `test_corrupt_final_screenshot_is_a_typed_error` |
| B12 | low | Typing a reason then clearing it left `missing_reasoning` dismissed and hid revert | clearing restores the flag; revert visible whenever an original exists | `clearing the reasoning is a reviewer edit that can be reverted` |
| B13 | low | Rebase dropped dismissals when a flag's detail text changed; `dropped` overwritten | dismissals match by code; `rebase_history` kept and exported | `rebase keeps a dismissal when only the flag's detail text changed`, `test_checklist_verdict_provenance_and_rewording_are_exported` |
| B14 | low | Heartbeat read-modify-write could overwrite `done`; unguarded writes aborted a job | one lock around status read-modify-write; writes never raise | `test_failure_is_isolated_and_everything_is_persisted` |
| B15 | low | Batch start failures invisible; unguarded `r.json()`; no spawn `error` listener in the desktop app | `startEngine` waits 2.5 s and reports an early exit; guarded fetch; `error` listeners | C27 |
| B16 | low | Recording text could close the untrusted-data wrapper | `<` escaped as `\u003c` | `test_recording_text_cannot_close_the_untrusted_block` |
| B17 | low | Session id pattern allowed `.` and `..` | `SAFE_ID` rejects them | C26 (traversal → 404) |
| B18 | low | Validator left zip copies in %TEMP%; didn't check images | temp dir removed on close; PNG signature and Claude screenshot size checked | `test_validator_rejects_non_png_images_and_zip_copies_are_cleaned` |
| B19 | low | AI runs set `review.edited`; `run.note` never shown; unused `review_stale` | AI never sets `edited`; note shown in End panel; field removed | viewer tests (AI never changes decisions) |
| B20 | nit | Reversed/multi/other-unit Range → 416 instead of ignoring the header | RFC 9110 handling | C26 |

Found while fixing (not in either review): the test bench was not actually topmost (C30), and the pipeline read the
system double-click time but not the box size (A17).

## Blocked checks

- Live Anthropic API calls (AI review, optional live validation of the Claude export): no credential available in this environment.
- Acoustic end-to-end audio latency (mic hears a beep at a known time): no loopback/acoustic path on this machine (C22).
- OCR of screenshots (`--ocr`): Tesseract is not installed on this machine; untested.
- AltGr / non-US keyboard layouts: unit-tested with synthetic key events only.
