import Link from "next/link";
import { notFound } from "next/navigation";
import { getSession, processingStatus, sessionDir } from "@/lib/sessions";
import Reviewer from "./Reviewer";

export const dynamic = "force-dynamic";

export default async function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const id = decodeURIComponent((await params).id);
  const s = await getSession(id);
  if (!s) {
    const dir = await sessionDir(id);
    if (!dir) notFound();
    const st = await processingStatus(dir);
    return (
      <main className="record">
        <div className="eyebrow"><Link href="/">← recordings</Link> · {id}</div>
        <h1>Not processed yet</h1>
        <p className="lede">
          {st?.state === "running" || st?.state === "queued" ? "Processing is in progress; this page will work once it finishes."
            : st?.state === "failed" || st?.state === "interrupted" ? `Processing ${st.state}: ${st.error ?? "unknown error"}.`
            : "This recording hasn't been transcribed and checked yet."}{" "}
          Go back to the recordings list and use <b>Process selected</b>.
        </p>
      </main>
    );
  }
  return <Reviewer id={id} initial={s.trajectory} hadReview={s.reviewed} rebased={s.rebased} />;
}
