/**
 * FNV-1a 32-bit over UTF-8 bytes, as 8 hex chars. Implemented identically in
 * processor/thinkaloud/review_inputs.py so an AI evaluation computed by the engine
 * can be compared with the current state in the browser (staleness).
 */
export function fnv1a(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let h = 0x811c9dc5;
  for (const b of bytes) {
    h ^= b;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

/** What a narration assessment judged: the step's action and its current reasoning. */
export function narrationInput(step: { uid?: string; id: number; description?: string; reasoning: string }): string {
  return fnv1a(`${step.uid ?? step.id}\n${step.description ?? ""}\n${step.reasoning ?? ""}`);
}

/** What a final-screen check judged: the item text, the criteria and the final screenshot. */
export function finalCheckInput(itemText: string, criteria: string, finalFile: string | null): string {
  return fnv1a(`${itemText}\n${criteria}\n${finalFile ?? ""}`);
}
