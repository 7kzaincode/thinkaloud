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

(appended as work proceeds)

## Review findings

(appended as reviews run)

## Blocked checks

- Live Anthropic API calls (AI review, optional live validation of the Claude export): no credential available in this environment.
