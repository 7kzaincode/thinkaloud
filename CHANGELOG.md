# Changelog

## 0.2.2 (2026-09-29)

- Closing the main window quits the app. After a recording, the hidden recording pill kept the app running with no
  window, and opening thinkaloud again showed nothing.
- The installer is now named `thinkaloud-setup-<version>.exe`.
- The final screenshot's caption no longer says "captured before the action".

## 0.2.1 (2026-09-28)

- Narration is matched per sentence: speech-to-text keeps word timings and gets the task text as context (names and
  punctuation), sentences are split where the speaker paused for an action, and each goes to the action it announces.
- Replaying a step plays everything said about it, then pauses; the timeline shades the selected step and labels its
  time; the chips under **Why** play from when each part was said.
- QC notes when an app hides what was clicked (for example the Arc browser).

## 0.2.0 (2026-09-27)

- **Settings → Speech recognition**: Fast, Balanced or Accurate (default) local models; switching re-transcribes.
- Recordings can be renamed (task and "done when"), deleted to **Recently deleted**, restored or deleted permanently.
- Replay and `space` play only the selected step; keyboard shortcuts no longer press a focused button.

## 0.1.0 (2026-09-26)

- First release: Windows recorder, processor (steps, before/after screenshots, narration alignment, QC), review
  app, validated exports (dataset and Claude computer-use), optional AI-assisted review (Anthropic or Gemini), batch
  processing (native or Docker) and the Windows desktop app.
