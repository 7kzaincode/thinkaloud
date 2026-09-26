# 60-second demo script

**Setup.**
- **Pick a task.** Choose a real task of about 40 seconds that involves a decision. Example: on Google Flights, find the cheapest *nonstop* Toronto → San Francisco flight two weeks out. The cheapest result usually has a stop, so you have to reason past it.
- **Clear the screen.** Close anything private, because the whole monitor is captured.
- **Record the video.** Use OBS or Xbox Game Bar (Win+Alt+R) alongside thinkaloud.
- **Prepare the app.** Open the thinkaloud desktop app. Optional: add an Anthropic key under Settings to show AI suggestions.

---

### 0:00–0:07 · Hook (voiceover over the Recordings page)

> "AI labs buy recordings of experts using computers. Recording clicks is easy. The hard part is capturing *why*
> they did each thing, and knowing whether they actually got it right. This is thinkaloud."

### 0:07–0:12 · New recording

1. Click **New recording**.
2. Type the task, then what "done" looks like: *"Nonstop flight selected, and it's the cheapest nonstop."*
3. Show the mic meter moving and click **Start recording**.

### 0:12–0:30 · Do the task while narrating (speed it up ~2× in editing)

Say it *before* each action:

> "Airport codes, not cities. Toronto has two airports and I want Pearson."
> "Sorting by price, because the default is 'best'."
> "The top result stops in Chicago. The task says nonstop, so I'm filtering instead."
> "Air Canada, nonstop, that's the one."

Point at the pill at the bottom (timer, mic level, Pause). Press **F9**. Processing runs and the review opens by itself.

### 0:30–0:50 · Review (the main shot)

1. **Before / After tabs** on a click step.
   > "For every action: the screen right before, the screen after it settled, what they did ('Clicked the Price button', straight from Windows accessibility), and why, in their own words, with when they said it."
2. **Scroll step.** Show a long scroll with a pause: one step, with the direction runs listed.
   > "A scroll is one action, not fifteen."
3. **Replay tab.** Press play and let the selected step follow the video. Click a step and the video seeks.
4. **Missing reasoning flag.** Press `n` to jump to it and type a sentence. The flag moves to dismissed, and the original is kept.
5. *(If a key is set)* **Check narration.** A suggestion appears with Accept / Dismiss.
   > "AI only suggests. Nothing changes until a person accepts it."

### 0:50–1:00 · End state and export

1. On **End state**, mark the checklist item *met*, then click **Task done** in the top bar.
2. Click **Export…**. The dialog shows "1 of 1 recordings exported" and that the bundle validated, in both the dataset format and Claude's computer-use format.

> "A reviewer checks the final screen against what 'done' meant. That's the part my last project, teachAR,
> never figured out."

---

**Tips**
- **Rehearse.** Do one take without the screen recorder to get the narration rhythm.
- **Fix transcripts on camera.** If Whisper mangles a word, fix it in the Why box. That's the product working.
- **Privacy flags.** If your task types an email, export will skip the recording until you check the privacy flags. Show that too, since it's a feature.
- **Readability.** Keep the app window large and the zoom at 110% so it reads on a phone.
