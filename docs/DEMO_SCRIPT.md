# 60-second demo script

**Setup before you hit record.** Pick a real task that takes about 40 seconds and involves a few
decisions. Good example: on Google Flights, find the cheapest *nonstop* Toronto → San Francisco
flight on a date two weeks out. It works because the cheapest result usually has a stop, so you have
to reason past it. Close anything private. Set your mic as the default input (not Voicemod).

Record the demo video with OBS or the Xbox Game Bar (Win+Alt+R) running at the same time as thinkaloud.

---

### 0:00–0:08 · Hook (face or voiceover over the terminal)

> "AI labs are buying recordings of experts using computers. The recording is easy. What's hard is
> capturing *why* they did each thing, and knowing whether they actually got it right. This is
> thinkaloud."

On screen: run

```
python recorder/record.py --task "Cheapest nonstop Toronto to SF, Oct 17" --criteria "Nonstop flight selected, cheapest one"
```

### 0:08–0:30 · Do the task while narrating (sped up to ~2x in editing)

Say it naturally, *before* each action:

> "Airport codes, not cities. Toronto has two airports and I want Pearson."
> "Sorting by price, because the default is 'best', which is basically ads."
> "The top result has a stop in Chicago. The task says nonstop, so I'm filtering instead of taking it."
> "Air Canada, nonstop, that's the one."

Press **F9**. Terminal prints `Saved sessions/... (41.2s, 63 events, 14 frames, audio=yes)`.

### 0:30–0:38 · Process

```
python -m thinkaloud ../sessions/<that folder>
```

> "Whisper transcribes it, raw events get merged into steps, and each sentence gets attached to the
> action it was explaining."

Let the QC summary line sit on screen for a beat: `14 steps, 1 missing reasoning, ...`

### 0:38–0:55 · Review in the viewer (the main shot)

1. Open the session. Point at the **timeline**: grey bars are narration, ticks are actions, the red
   tick is a privacy flag.
   > "Every step: the screen they saw, what they did, and why, in their own words."
2. Press `→` a couple of times. The click marker moves on the screenshot and the serif reasoning changes.
3. Press `n` to jump to a flag. If you get a real *missing reasoning* flag, type a sentence into the
   box and show that the flag moves to dismissed.
   > "QC catches steps with no explanation, long silent pauses, and anything that looks like a
   > password or an email, which gets redacted before it leaves the machine."
4. *(Optional, 3 s)* switch to the synthetic sample to show the redacted password step, if your
   real task didn't have one.

### 0:55–1:00 · Close

Press `e` for **End state**, click **Task done**, then **Export JSON**.

> "And a reviewer checks the final screen against what 'done' was supposed to mean. That's the part
> my last project, teachAR, never figured out."

---

**Tips**
- Do one take of the task without the video recorder first to get the narration rhythm down.
- If Whisper mangles a word, fix it in the viewer on camera. That's the product working.
- Keep the terminal font large (18pt+) and the browser at 110% zoom so it reads on a phone.
