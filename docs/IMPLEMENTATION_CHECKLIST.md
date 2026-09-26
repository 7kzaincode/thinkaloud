# Implementation checklist

Status legend: NOT STARTED / IN PROGRESS / IMPLEMENTED (code exists) / VERIFIED (acceptance check ran and passed) / BLOCKED.
Evidence for every VERIFIED row is in [VALIDATION_LOG.md](VALIDATION_LOG.md).

## Phase A: correct recorded data

| ID | Requirement | Implementation | Acceptance checks | Status |
|---|---|---|---|---|
| A1.1 | Scroll grouping is event-driven, not a 1.5 s timer | `processor/thinkaloud/steps.py` | continuous scroll > 1.5 s stays one step | NOT STARTED |
| A1.2 | Split on click/type/key, context change, long pause (configurable), stop | `steps.py` | scroll→click→scroll; long pause; window change; stop during scroll | NOT STARTED |
| A1.3 | Preserve raw scroll events, direction runs, units | `steps.py`, recorder raw wheel delta | direction change keeps both runs; fractional deltas kept | NOT STARTED |
| A1.4 | Screenshot settling separate from step boundaries; settled capture when scrolling ends; flush at stop | `recorder/record.py` settle stills + end frame | recorder test: settle still after scroll; final step flushed | NOT STARTED |
| A2.1 | Before + after observation per step with capture timestamps and status | recorder frame ring + stills index; `processor/thinkaloud/observations.py` | ownership tests: before captured before t_start; after captured in (t_end, next.t_start] | NOT STARTED |
| A2.2 | Monotonic timebase + documented clock mappings | recorder meta `clocks`; README | timeline doc; audio/video mapping tests | NOT STARTED |
| A2.3 | Rapid actions, delayed render, capture failure, final action handled explicitly | observations.py statuses | missing/unsettled/stale statuses tested | NOT STARTED |
| A2.4 | Coordinate-space metadata (dims, origin, scaling) | meta `coordinate_space`, trajectory | coordinate transform tests incl. offset monitor | NOT STARTED |
| A2.5 | Schema versioned; v0.1 recordings still load, missing after stays missing | pipeline legacy path | legacy fixture test | NOT STARTED |
| A2.6 | Before/after inspection in viewer | viewer Reviewer | UI check | NOT STARTED |
| A3.1 | UIA target metadata (role, name, rect, ids, window, process, URL when reliable, status, latency) | `recorder/uia.py` | unit tests with fake UIA; live Windows check on test bench | NOT STARTED |
| A3.2 | Non-blocking lookups, timeouts, fallback to coordinates | uia worker thread + UIA timeouts | timeout/unsupported/fallback tests | NOT STARTED |
| A3.3 | Viewer describes "Clicked the Add to Cart button", keeps raw coords/metadata | viewer format.ts | UI check + unit test | NOT STARTED |
| A4.1 | Screen video ~4 FPS (configurable), bounded memory, encoder failure handling | recorder video writer (PyAV libx264, MKV) | recorder test; resource cleanup test | NOT STARTED |
| A4.2 | Playback with narration audio; play/pause/seek; step click seeks; active step highlight | processor muxes playback.mp4; viewer Playback | UI check | NOT STARTED |
| A4.3 | Shared timeline; dropped frames/offsets don't accumulate | VFR PTS = capture time; audio offset from clock anchors | multi-minute alignment measurement ≤ 250 ms | NOT STARTED |
| A4.4 | Missing audio/video handled clearly | viewer states | UI check with fixture lacking media | NOT STARTED |

## Phase B: reviewable, useful downstream

| ID | Requirement | Implementation | Acceptance checks | Status |
|---|---|---|---|---|
| B1.1 | Vendor-neutral dataset export (versioned, manifest, assets) | `processor/thinkaloud/export.py` | validator loads bundle; sha256 of assets | NOT STARTED |
| B1.2 | Claude computer-use export mapped to `computer_toolset_20260801` (documented) | export.py | action mapping tests for every action type | NOT STARTED |
| B1.3 | Narration kept distinct from edits/AI; timing preserved; missing stays missing | export.py | tests | NOT STARTED |
| B1.4 | Validation errors/warnings for unsupported/ambiguous actions | export.py validator | tests | NOT STARTED |
| B1.5 | Standalone loader/validator + fixtures | `processor/thinkaloud/dataset.py` (copied into bundles) | independent load of exported bundle | NOT STARTED |
| B1.6 | One-click export in UI with accurate success/failure/warnings | viewer API + UI | UI check | NOT STARTED |
| B2.1 | Anthropic integration, configurable model, key never in browser/logs | `processor/thinkaloud/ai_review.py`, viewer API | fixture tests; live: BLOCKED unless key present | NOT STARTED |
| B2.2 | Narration assessment, missing/filler detection | ai_review.py | fixture tests | NOT STARTED |
| B2.3 | Checklist drafting from "done when"; editable | ai_review.py + viewer | fixture + UI | NOT STARTED |
| B2.4 | Final-screen check: supported / contradicted / unknown | ai_review.py | fixture tests | NOT STARTED |
| B2.5 | Suggestions never auto-apply; human decisions + provenance persisted; staleness on edits | viewer lib/review.ts | node tests on state transitions | NOT STARTED |
| B2.6 | Structured output validation; malformed/timeout/rate limit handling; disclosure before sending | ai_review.py + UI | fixture tests | NOT STARTED |
| B3.1 | Batch view with identity, status, steps, narration %, words/step, review status, warnings | viewer /batch + lib/metrics.ts | metric unit tests; UI check | NOT STARTED |
| B3.2 | Filters/navigation + batch processing controls | viewer | UI check | NOT STARTED |
| B3.3 | Docker batch processing: bounded concurrency, per-recording status, retries, idempotent, restart | `processor/thinkaloud/batch.py`, docker-compose | docker run with valid + invalid fixtures | NOT STARTED |

## Integration

| ID | Requirement | Status |
|---|---|---|
| I1 | Full journey: task → record → process → inspect before/after → replay → AI suggestions → human decisions → batch metrics → export | NOT STARTED |
| I2 | Edits and review decisions persist across reloads; reprocessing keeps compatible human edits | NOT STARTED |
| I3 | Recording state clear; stop and pause controls; password fields masked where detectable | NOT STARTED |
| I4 | Independent review A (correctness/data integrity) done, findings fixed | NOT STARTED |
| I5 | Independent review B (product/adversarial) done, findings fixed | NOT STARTED |
