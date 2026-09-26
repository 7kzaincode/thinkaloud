# thinkaloud

A prototype recorder for think-aloud computer-use demonstrations. An expert does a real task on their
Windows computer while talking through it. thinkaloud records what they did (clicks, typing, scrolls),
what the screen looked like **before and after** each action, a low-frame-rate screen video, and why
they did it (their narration). It turns that into reviewable steps, checks the result for problems,
lets a person review and correct it (optionally with AI suggestions they confirm or reject), and
exports validated datasets, including a representation using Anthropic's computer-use toolset.

It started as a weekend project and grew a lot past that; it is still a prototype. Read
[Known limitations](#known-limitations) before trusting it with anything important.

## Why

I built this while applying to AfterQuery, who sell expert-demonstrated computer-use trajectories and
reasoning data to AI labs ("models trained on outputs plateau, models trained on reasoning improve").
It comes out of an earlier project, teachAR, which recorded an expert's hand movements in VR. Recording
the motion was easy; knowing whether the task was actually done right was hard. So thinkaloud records
**what "done" means** before recording starts, and a human checks the final screen against it. Only a
recording a person marked *Task done* is a positive demonstration.

## The workflow

```
New recording (task + "done when")          Recordings page                    Review page
   │  mic check, Record, F8 pause, F9 stop      │ process (native or Docker)        │ before / after / replay
   ▼                                            ▼                                   ▼
recorder ─► sessions/<id>/ ─► processor ─► trajectory.json + playback.mp4 ─► reasoning edits, flags, checklist,
 (Windows)   events, stills,   (steps, before/after,                              AI suggestions (confirm/reject),
             video, audio      narration, QC)                                     outcome ─► validated export bundle
```

| Part | Where | What it does |
|---|---|---|
| Recorder | `recorder/` (`record.py`, `uia.py`, `winctx.py`) | Input events with window context and UI Automation targets, before/settled/end stills, 4 fps H.264 screen video, 16 kHz narration, password-field masking, pause. |
| Processor | `processor/thinkaloud/` | Whisper transcription, event-driven step segmentation, before/after pairing, narration alignment with timing, QC, `playback.mp4`. |
| Exports | `export.py`, `dataset.py` | Vendor-neutral dataset + Claude computer-use representation, manifest with checksums, standalone validator. |
| AI review | `ai_review.py` | Narration checks, checklist drafts, final-screen checks via the Anthropic API. Suggestions only. |
| Batch | `batch.py`, `docker-compose.yml` | Many recordings, bounded concurrency, persistent status, retries, restart handling. Native or Docker. |
| Viewer | `viewer/` (Next.js) | Recordings/batch page, review page, record screen, settings. |
| Desktop app | `desktop/` (Electron), `engine/` (PyInstaller) | One Windows app wrapping all of the above; installer bundles Python. |
| Sample | `samples/synthetic-flight/` | A synthetic 0.2 session (with narration audio and video) that exercises every rule. |

## Quick start

### Desktop app

Install `desktop/dist/thinkaloud Setup 0.1.0.exe` (unsigned: SmartScreen shows "More info" → "Run anyway"),
or run it from source (below). Then:

1. **New recording**: type the task and what "done" looks like, pick your microphone (the level meter
   should move when you talk; virtual devices are called out), **Start recording**.
2. After a 3-second countdown the window gets out of the way. A pill at the bottom of the screen shows
   the timer, mic level, **Pause** (F8) and **Stop** (F9). The pill is excluded from screen capture and
   its clicks are not recorded. While paused nothing is captured.
3. When you stop, the recording is processed and opens for review.

Recordings are saved in `Documents\thinkaloud\sessions`, exports in `Documents\thinkaloud\exports`, the speech
model (~150 MB, first run) in `Documents\thinkaloud\models`.

### From source (development)

Requires Windows 10/11 for recording, Python 3.12, Node 22.

```bash
python -m venv .venv
.venv/Scripts/pip install -r recorder/requirements.txt -r processor/requirements-dev.txt
cd viewer && npm install && cd ..
cd desktop && npm install && npm start          # the desktop app (viewer via `next dev`, engine from .venv)
```

Or the parts separately:

```bash
.venv/Scripts/python recorder/record.py --task "..." --criteria "..."   # F8 pause, F9 stop; --fps, --settle, --no-uia, --no-video
cd processor && ../.venv/Scripts/python -m thinkaloud ../sessions        # process everything new
cd viewer && npm run dev                                                 # http://127.0.0.1:3217 (reads ../sessions and ../samples)
```

### Batch processing in Docker

Capture runs natively on Windows; the container only post-processes saved recordings.

```bash
docker compose build
docker compose run --rm processor                                   # every recording in ./sessions
THINKALOUD_CONCURRENCY=4 docker compose run --rm processor
docker compose run --rm processor batch /data/sessions --jobs /data/jobs --force   # reprocess all
```

Status goes to `./jobs/<job>.json` and each recording's `processing.json`, which the Recordings page shows.
The same command runs natively: `python -m thinkaloud batch sessions --jobs jobs --concurrency 2`.

## Reviewing

The **Recordings** page lists every recording with processing status, steps, how many steps have their
own narration (count and %), narration words per step, open flags, the human decision and checklist
progress, and warnings. Filter, sort, select, **Process selected** (with a worker count), **Export selected**.

Denominators: *narrated* = steps with at least one narration segment of their own (carried or
reviewer-written reasoning does not count) ÷ all steps; *words / step* = narration words attached to steps
÷ all steps. Review counts are human decisions only; AI suggestions never count.

On a **review page**:

- **Before / After / Replay** (`b`, `a`, `space`). Each image says when it was captured relative to the
  action and what its status means. A green box marks the UI element that was clicked when UI Automation
  identified it; the ring marks the click. Missing images say why they are missing.
- **Replay** plays the screen video with the narration. The timeline shows narration, steps, idle gaps and a
  playhead; clicking the timeline seeks, selecting a step seeks to half a second before it, and while
  playing the selection follows the replay ("follow the replay").
- **What they did** describes the action ("Clicked the Add to Cart button") with the raw coordinates and
  UI Automation metadata one click away. **Why** is the reasoning: from narration (with chips saying whether
  it was said before, during or after the action), carried from an earlier step, or written by you. Your
  edits (including clearing it) keep the original and can be reverted.
- **End state**: a checklist derived from "done when", your verdict per item, notes, and the outcome. If you
  reword an item after deciding it, the page says the verdict was given for the earlier wording.
- Edits autosave to `trajectory.reviewed.json` (one save in flight at a time; pending edits are saved before an
  export and when leaving the page, and the browser warns if a save is still pending). The processor's
  `trajectory.json` is never modified by review, and the server merges only review-owned fields (reasoning,
  flags, dismissals, the review block) onto it, so a crafted review file can't change actions or image paths.
  If a recording is reprocessed, even while the page is open, your review is carried over to the new steps
  (matching step ids, or start time + action type for older recordings; dismissals match by flag code) and
  anything that no longer applies is listed and kept in `rebase_history`.

### AI-assisted review (optional)

Buttons on the End state panel: **Draft checklist from "done when"**, **Check final screen against checklist**
(supported / contradicted / unknown per item; unknown when it isn't visible), **Check narration on every step**
(explains / partially / does not explain / filler). Nothing is sent until you tick the consent box and click;
each button says exactly what it sends.

Every result is a suggestion: drafts must be accepted (optionally edited) to join the checklist, a final-screen
check must be adopted with "Use this as my verdict" (offered whenever the latest check disagrees with your current
verdict; the adopted run is stored as `accepted_from`), a narration assessment must be accepted to become a
reviewer flag. Suggestions never change reasoning, verdicts or the outcome, and a later AI run never overwrites
a decision you made. Each suggestion stores a hash of
what it judged; if you edit that input it is marked **stale** and can't be accepted until re-run.

Configure: in the desktop app, **Settings → Anthropic API key** (stored encrypted with Windows DPAPI via
Electron `safeStorage`, never shown again, never sent to the page); or set `ANTHROPIC_API_KEY` for the viewer
server. Without it, everything else works and the panel says why AI is unavailable.

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | – | credentials (or an `ant auth login` profile) |
| `THINKALOUD_AI_MODEL` | `claude-opus-5` | model id |
| `THINKALOUD_AI_EFFORT` | (model default) | `low`…`max` |
| `THINKALOUD_AI_TIMEOUT` / `THINKALOUD_AI_MAX_RETRIES` | 120 s / 2 | per request; the SDK retries 408/409/429/5xx |
| `THINKALOUD_AI_FALLBACKS` | `default` | server-side refusal fallback (`server-side-fallback-2026-07-01`); `off` to disable |

Responses are JSON-schema constrained (`output_config.format`) and validated again; malformed output is retried
once. Recording content is framed as untrusted data and the model is told not to follow instructions in it.

## Exports

**Export…** on a review page or **Export selected** on the Recordings page builds a bundle in `exports/`,
validates it, and offers a `.zip`. The dialog says how many of the selected recordings were exported, lists
any that were skipped with the reason, and reports bundle validation separately from actions that couldn't be
represented.

**Privacy gate.** A recording with an open high-severity privacy flag (typed email or secret, a redacted value
that may be visible on screen, an email or sensitive-looking URL parameter in a window title, element name or
page URL) is skipped. Dismiss the flags after checking the images, or click **I checked the privacy flags:
export anyway** (`engine export --allow-privacy-flags`).

```
thinkaloud-export-<time>-<n>rec-<random>/
  manifest.json                      formats, recordings (with the human outcome), every file + SHA-256
  README.md, thinkaloud_dataset.py   loader/validator, standard library only
  recordings/<id>/trajectory.json    thinkaloud.dataset/1.0
  recordings/<id>/assets/            before/after/final screens (PNG, full resolution); playback.mp4 if chosen
  claude/<id>.json                   thinkaloud.claude_computer_use/1.0
  claude/assets/<id>/                screenshots scaled for the tool (long edge ≤ 1920 px)
```

- **`thinkaloud.dataset/1.0`** (vendor-neutral): task and "done when"; stable recording and step ids; ordered
  actions in frame pixels (scroll keeps every raw wheel event and direction runs); before/after observations
  with capture times and status; UI Automation target; window context; narration **verbatim with timing**
  (`before_action` / `during_action` / `after_action`) and source "human narration (speech-to-text)"; current
  reasoning with its source and the original if a reviewer edited it; flags and dismissed flags; AI assessments
  marked `suggestion_only` with the human decision; review outcome (`outcome_source: "human"`), checklist
  (with `accepted_from` for adopted AI checks and `verdict_outdated` when an item was reworded after its
  verdict), notes, `rebase_history`; coordinate space; timeline. Typing that was corrected keeps the raw
  `keystrokes` (`"helo⌫lo"`); drags carry start and end points.
- **`thinkaloud.claude_computer_use/1.0`**: the recording as a conversation using Anthropic's
  **`computer_toolset_20260801`** (checked against the official computer-use tool docs on 2026-09-26): member
  tools as `tool_use.name` with `toolset_name: "computer"`, coordinates in the scaled screenshot space. Each step
  is an assistant turn with the action call(s) and a `screenshot` call, answered by `OK` results and the
  after-state image. Mapping: left click ×1/2/3 → `left_click`/`double_click`/`triple_click`; right/middle →
  `right_click`/`middle_click`; drag → `left_click_drag`; modifiers → `text`; typing → `type`; Backspace that
  deletes existing text → `key BackSpace` (with `repeat`); keys → `key` with xdotool names (`Return`, `ctrl+c`,
  `Page_Down`, `slash`, `repeat` ≤ 100); scroll → one `scroll` per direction run (`scroll_direction`, whole-notch
  `scroll_amount`, rounded half up and reported; a run under half a notch is an error). Narration, review and QC are in
  `annotations`, never in the conversation; narration is not presented as model reasoning. Anything that can't
  be represented faithfully (QC-redacted text, masked password input, off-screen clicks, unknown keys) is left
  out and listed in `errors`, and the file is marked `valid_for_training: false`. Typing that was entered and
  then deleted again is kept as a step (warning), since replaying it as `type ""` would be meaningless. Image blocks use
  `{"type": "thinkaloud_asset"}` sources; `Bundle.claude_messages(id)` returns API-ready base64.

```bash
python thinkaloud_dataset.py path/to/bundle        # or the .zip; exit code 1 if invalid
```
```python
import thinkaloud_dataset as td
b = td.load_bundle("bundle.zip")                   # validates; raises on errors
for rec in b.recordings(): ...
messages = b.claude_messages(rec["recording"]["id"])
```

The validator checks manifest checksums, schema, step order, that every *before* image finished capturing before
its action and every *after* image started after the action ended and finished before the next one began,
coordinates, references, and that every image is a real PNG (for the Claude file, of the declared screenshot
size); for the Claude file: message alternation, member names and inputs (including key names), coordinates
inside the screenshot, one result per call with `toolset_name`. A `.zip` is extracted to a temporary folder
that is removed when the bundle is closed (`with td.load_bundle(...) as b:`).

## Data formats and timeline

### Session folder (recorder 0.2, `thinkaloud.session/0.2`)

| File | Contents |
|---|---|
| `events.jsonl` | ordered input events: `seq`, `t`, `type` (click/key/scroll/marker), coordinates (physical screen px), `window` (hwnd, title, process), `target` (UI Automation: role, name, automation id, rect, URL when the page's Document exposes it, status, latency), raw wheel `raw` delta and `dx`/`dy` in notches (1 = 120), `mods`, `masked`, `injected`, `before_seq` |
| `frames/stills.jsonl` + `frames/f*.png` | every still request: `kind` (start/before/settled/end), capture start/end, status (ok/duplicate/dropped/missing/error) |
| `screen.mkv`, `video_frames.jsonl` | H.264, no B-frames, one frame per capture, PTS = capture midpoint in ms on the recording clock |
| `audio.wav` | 16 kHz mono; `meta.audio.offset_s` places sample 0 on the recording clock |
| `meta.json` | task, criteria, clock, coordinate space (frame origin/size, monitor DPI scale), capture/video/audio stats, UIA availability, pauses, errors |

### Timeline

Everything is on one clock: `time.perf_counter()` minus the value at recording start. Screen captures record
grab start and end. Audio: PortAudio's stream clock is the same QueryPerformanceCounter clock, but MME gives no
ADC timestamps, so sample 0 is placed at the earliest callback's bound minus the reported input latency; the
sound card's drift against the clock is measured (tens of ppm here) and reported, not corrected.
`playback.mp4` uses the same timeline (media time = recording time): video packets are copied with their capture
PTS (gaps stay gaps; nothing shifts), audio is padded/trimmed to its offset.

**Before/after contract.** A capture thread grabs the screen every 1/fps into a small ring. When input starts a
possible new action, the latest frame whose grab *finished* before the event is kept as its *before* still, so a
before image can never contain its own action. When input goes quiet for `settle` seconds (default 0.6) the next
frame is kept as *settled*; a final *end* frame is grabbed after stop. The processor pairs each step with:

| before | meaning | after | meaning |
|---|---|---|---|
| `ok` | captured before the action and after the previous one ended | `settled` | ≥ settle s after the action, before the next |
| `predates_previous_action` | before this action, but the previous one hadn't finished | `unsettled` | sooner; screen may still be changing |
| `stale` | more than max(1 s, 3 frames) old | `missing` | next action began before any new capture |
| `missing` / `at_action` / `legacy_earlier_action` | nothing / recorder 0.1 statuses | | |

Video frames fill gaps when no still qualifies (marked `source: "video"`, lossy). Nothing is invented.

### Steps (trajectory 0.2)

Scrolls form one step until a different kind of input, a change of window or modifiers, a pause longer than
`scroll_pause_s` (5 s), a pause/stop marker, or the end: never because a timer ran out. Typing merges within
`type_gap_s` (2 s) in the same window; Enter/Tab and shortcuts are their own steps; repeats of a special key merge;
a Backspace with no typed text before it is its own `key` step (it deletes text that was already there);
double/triple clicks use the system double-click time and box and never merge across a pause; a press and release
more than 5 px apart is a `drag` step. Steps have stable `uid`s (`s` + first event seq).
`trajectory.json` also carries `observations`, `target` (+ `frame_rect`, `reliable` = looked up within 500 ms),
`description`, `context`, `narration` (segment ids + timing), `media`, `coordinate_space`, `timeline`,
`final_observation`. Recorder 0.1 recordings still process (`source.legacy: true`) with explicit legacy statuses.

## Privacy

- `sessions/`, `exports/`, `jobs/`, recordings' media and review files are gitignored.
- Keys typed into fields Windows reports as password fields (UI Automation `IsPassword`) are replaced with `•`
  before anything is written. The focused field is looked up again whenever focus may have moved (a click, Tab,
  Enter, a shortcut, a new typing burst), so a password typed right after Tab is masked. Masking **fails closed**:
  if the focused field can't be checked (UI Automation timed out or errored), the keys are masked too and the
  recording gets a `masked_unknown_focus` flag. Typed text that looks like an email or password elsewhere is flagged and redacted
  from exports (`[REDACTED]`), but **the screen may still show it**: QC raises `redacted_value_on_screen`, and
  `--ocr` (Tesseract) flags emails visible in screenshots. Screenshots are not blurred.
- The recorder captures the whole monitor while recording. Use Pause for anything private.
- AI review sends only what each button lists, only after consent. The API key never reaches the browser.
- The viewer listens on 127.0.0.1 only, and its API refuses requests whose Host isn't loopback, cross-origin
  requests, and writes that aren't `application/json`. Frame and media paths are confined to the recording folder.
- Exports skip recordings with open high-severity privacy flags unless you explicitly override (see Exports).

## Verification

```bash
cd processor && ../.venv/Scripts/python -m pytest -q        # segmentation, ownership, A/V, export, AI boundary, batch
cd viewer && npm test && npx tsc --noEmit                    # review state, staleness, rebase, metrics
python scripts/e2e_capture.py                                # native Windows: real input into a test window (see below)
python scripts/e2e_capture.py --flash-seconds 180 --flash-interval 3   # multi-minute sync measurement
```

`scripts/e2e_capture.py` drives a controlled Chromium test page (`scripts/testbench`) with injected mouse and
keyboard input while the real recorder runs, then checks UI Automation targets, password masking (clicked into and
tabbed into), scroll grouping, before/after pixel colours, and video-vs-input timing. It moves your mouse; the test
window is always-on-top (the driver refuses to start if it isn't) and every click, scroll and keystroke is preceded
by a check that the test window is in front and under the cursor (it aborts otherwise).
`node scripts/app_journey.mjs` runs the whole journey in the packaged desktop app (record → process → review →
replay → export → reload) with the same guarded input driver.
Results and measurements are in [docs/VALIDATION_LOG.md](docs/VALIDATION_LOG.md) and `docs/evidence/`;
[docs/IMPLEMENTATION_CHECKLIST.md](docs/IMPLEMENTATION_CHECKLIST.md) maps every requirement to its evidence.

Measured on this machine (2560×1440, 4 fps): 60/60 screen changes in a 199 s run appeared in the first frame
captured after the input, never earlier; event → first frame showing it 16–273 ms (one frame period + render);
no dropped frames; audio clock drift 22.7 ppm.

## Known limitations

- **Windows only for capture.** UI Automation, window context and DPI handling are Win32; the processor, viewer
  and Docker batch run anywhere.
- **Before images can be up to one frame period old** (≈250 ms at 4 fps), so a hover menu that appeared just
  before a click may be missing from the before image. Raise `--fps` for more precise before-states (more CPU).
- **UI Automation is best effort.** Apps that don't implement it (games, some Java/Electron apps without
  accessibility enabled, remote desktops) give `pane`/`custom` or nothing; when a hit test lands on a container
  (document, pane, group) the recorder walks down to the deepest element under the point within 150 ms. Lookups on
  unresponsive apps are bounded (600 ms) and a result that arrives after 500 ms is not used for descriptions.
  URLs are only recorded when the page's Document element exposes one.
- **Password masking depends on the app reporting a password field.** Custom password widgets that don't set
  `IsPassword` are captured; QC heuristics then flag and redact likely secrets in exports, but screenshots may
  show them. Masking also means the exact typed text is unknown, so masked steps are errors in the Claude export.
- **Audio alignment** is placed from callback timing (± the device's reported latency, 26 ms here) and drift is
  measured, not corrected. End-to-end acoustic latency against a reference beep could not be measured on the
  development machine (no loopback device).
- **Scroll amounts are wheel notches**; the Claude export rounds runs to whole notches (reported).
- **Hover and mouse moves are not recorded.** Drags are recorded as start and end points only (no path), and
  only for a press and release of the same button with nothing in between.
- **AI review was only tested against a fake client** (no API key in the development environment); the live
  API path is implemented to the documented SDK interface (anthropic 1.8.0) but unverified.
- **Non-US keyboard layouts** were covered by unit tests only (AltGr characters are recorded as typed text);
  not checked on a real non-US layout.
- **The OCR check (`--ocr`) is untested**: Tesseract isn't installed on the development machine; the Docker
  image can include it (`--build-arg WITH_OCR=1`).
- **The installer is unsigned**, ~190 MB, Windows x64 only.
- One monitor is captured at a time (`--monitor`); clicks on other monitors are recorded but flagged outside the frame.

## Layout

```
recorder/    record.py (capture), uia.py (UI Automation worker), winctx.py (window/DPI helpers)
processor/   thinkaloud/ steps, observations, align, qc, describe, media, pipeline, export, dataset,
             ai_review, review_inputs, batch, commands; tests/ (+ fixtures/legacy-v01); Dockerfile
viewer/      Next.js: app/ (Batch, record, settings, s/[id] review), app/api/, lib/ (review, metrics, sessions, engine)
desktop/     Electron shell: main.js, preload.js, pill.html; installer config
engine/      engine.py (single entry point for the app) + build.ps1 (PyInstaller)
scripts/     make_synthetic.py, e2e_capture.py + testbench/, smoke_record.py
samples/     synthetic-flight/ (synthetic data only)
docs/        IMPLEMENTATION_CHECKLIST.md, VALIDATION_LOG.md, evidence/, DEMO_SCRIPT.md
```
