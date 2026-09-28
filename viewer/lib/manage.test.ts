// Deleting / restoring recordings and the speech-model setting, on throwaway folders.
// Run: npm test (the server-side modules need --conditions=react-server; see package.json)
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

const root = mkdtempSync(path.join(tmpdir(), "thinkaloud-manage-"));
const SESSIONS = path.join(root, "sessions");
const SAMPLES = path.join(root, "samples");
process.env.THINKALOUD_DIRS = `${SESSIONS};${SAMPLES}`;
process.env.THINKALOUD_DATA = root;
delete process.env.THINKALOUD_WHISPER_MODEL;

const S = await import("./sessions.ts");
const speech = await import("./speech.ts");

function recording(dir: string, id: string, opts: { meta?: boolean; task?: string } = {}) {
  const d = path.join(dir, id);
  mkdirSync(d, { recursive: true });
  writeFileSync(path.join(d, "events.jsonl"), "");
  if (opts.meta !== false) writeFileSync(path.join(d, "meta.json"), JSON.stringify({ task: opts.task ?? `task ${id}`, success_criteria: "done" }));
  return d;
}

test("deleting moves a recording to Recently deleted, and restoring brings it back", async () => {
  const d = recording(SESSIONS, "rec-a", { task: "Book a flight" });
  writeFileSync(path.join(d, "trajectory.reviewed.json"), "{}");
  const r = await S.trashSessions(["rec-a"]);
  assert.deepEqual(r, { moved: ["rec-a"], refused: [] });
  assert.equal(existsSync(d), false);
  const items = await S.listTrash();
  assert.equal(items.length, 1);
  assert.equal(items[0].id, "rec-a");
  assert.equal(items[0].task, "Book a flight");
  assert.ok(existsSync(path.join(root, "trash", "rec-a", "trajectory.reviewed.json")));  // the review goes with it

  const back = await S.restoreTrash(items[0].name);
  assert.deepEqual(back, { ok: true, id: "rec-a" });
  assert.ok(existsSync(path.join(d, "trajectory.reviewed.json")));
  assert.equal(existsSync(path.join(d, ".deleted.json")), false);
  assert.equal((await S.listTrash()).length, 0);
});

test("deleting the same id twice keeps both copies; restoring onto an existing recording is refused", async () => {
  recording(SESSIONS, "rec-b");
  await S.trashSessions(["rec-b"]);
  recording(SESSIONS, "rec-b");
  await S.trashSessions(["rec-b"]);
  const copies = (await S.listTrash()).filter((i) => i.id === "rec-b");
  assert.equal(copies.length, 2);
  assert.notEqual(copies[0].name, copies[1].name);
  assert.deepEqual(await S.restoreTrash(copies[0].name), { ok: true, id: "rec-b" });
  const clash = await S.restoreTrash(copies[1].name);
  assert.equal(clash.ok, false);
  assert.equal(!clash.ok && clash.status, 409);
});

test("samples, recordings in progress and recordings being processed are not deleted", async () => {
  recording(SAMPLES, "sample-1");
  recording(SESSIONS, "live", { meta: false });               // no meta.json, events.jsonl just written
  const busy = recording(SESSIONS, "busy");
  writeFileSync(path.join(busy, "processing.lock"), "{}");
  const r = await S.trashSessions(["sample-1", "live", "busy", "nope"]);
  assert.deepEqual(r.moved, []);
  assert.deepEqual(r.refused.map((x) => x.id), ["sample-1", "live", "busy", "nope"]);
  assert.match(r.refused[0].reason, /sample/);
  assert.match(r.refused[1].reason, /still being recorded/);
  assert.match(r.refused[2].reason, /being processed/);

  // a crashed recording (no meta.json, nothing written for a while) can be cleaned up
  const old = Date.now() / 1000 - 600;
  utimesSync(path.join(SESSIONS, "live", "events.jsonl"), old, old);
  assert.deepEqual((await S.trashSessions(["live"])).moved, ["live"]);

  const list = await S.listSessions();
  assert.equal(list.find((s) => s.id === "sample-1")?.readonly, true);
  assert.equal(list.find((s) => s.id === "busy")?.readonly, false);
  assert.equal(await S.ownSessionDir("sample-1"), null);
});

test("permanent deletion only touches Recently deleted", async () => {
  recording(SESSIONS, "rec-c");
  await S.trashSessions(["rec-c"]);
  const before = (await S.listTrash()).length;
  assert.equal(await S.purgeTrash(".."), 0);                   // nothing outside the trash folder
  assert.equal(await S.purgeTrash("rec-c"), 1);
  assert.equal((await S.listTrash()).length, before - 1);
  assert.ok(existsSync(SESSIONS));
  assert.ok(await S.purgeTrash(null) >= 1);                    // empty everything
  assert.equal((await S.listTrash()).length, 0);
  assert.ok(existsSync(path.join(SESSIONS, "busy")));          // recordings themselves untouched
});

test("the speech model setting: default, saved choice, environment override", async () => {
  assert.deepEqual(await speech.speechModel(), { id: "large-v3-turbo", source: "default" });
  await speech.setSpeechModel("small.en");
  assert.deepEqual(await speech.speechModel(), { id: "small.en", source: "settings" });
  assert.equal(JSON.parse(readFileSync(path.join(root, "settings.json"), "utf-8")).speech_model, "small.en");
  await assert.rejects(speech.setSpeechModel("tiny; rm -rf"), /unknown speech model/);
  process.env.THINKALOUD_WHISPER_MODEL = "base.en";
  assert.deepEqual(await speech.speechModel(), { id: "base.en", source: "environment" });
  delete process.env.THINKALOUD_WHISPER_MODEL;
});

test("the viewer's model table matches the engine's", () => {
  const py = readFileSync(new URL("../../processor/thinkaloud/transcribe.py", import.meta.url), "utf-8");
  for (const m of speech.SPEECH_MODELS) assert.ok(py.includes(`"${m.id}"`), `${m.id} missing from transcribe.py`);
  assert.ok(py.includes(`DEFAULT_MODEL = "${speech.DEFAULT_SPEECH_MODEL}"`));
});
