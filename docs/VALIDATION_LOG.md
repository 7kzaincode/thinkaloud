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
| C22 | Acoustic audio alignment on this machine | Stereo Mix (WDM-KS) at 16/48 kHz | **BLOCKED**: PortAudio host error; output is a USB headset, so no loopback or acoustic path to measure mic latency against a known beep. Covered instead by: synthetic A/V placement test (C6) and measured clock drift (C21) |

## Review findings

(appended as reviews run)

## Blocked checks

- Live Anthropic API calls (AI review, optional live validation of the Claude export): no credential available in this environment.
- Acoustic end-to-end audio latency (mic hears a beep at a known time): no loopback/acoustic path on this machine (C22).
