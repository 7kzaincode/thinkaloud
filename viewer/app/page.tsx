import Link from "next/link";
import { listSessions } from "@/lib/sessions";
import { fmtTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Home() {
  const sessions = await listSessions();
  return (
    <main className="index">
      <div className="index-head">
        <div>
          <div className="eyebrow">thinkaloud · review queue</div>
          <h1>Sessions</h1>
        </div>
        <Link href="/record" className="btn primary big">New recording</Link>
      </div>
      <p className="lede">
        Each session is one expert doing one task while narrating. Step through what they did and why,
        fix the reasoning, clear or raise flags, then mark whether the task was actually done.
      </p>
      {sessions.length === 0 ? (
        <div className="empty">
          No processed sessions found. Run <code>python -m thinkaloud ../sessions</code> in{" "}
          <code>processor/</code>, or generate the sample with <code>python scripts/make_synthetic.py</code>.
        </div>
      ) : (
        sessions.map((s) => (
          <Link key={s.id} href={`/s/${encodeURIComponent(s.id)}`} className="session-row">
            <div>
              <div className="task">{s.task || <em>Untitled task</em>}</div>
              <div className="meta mono">
                {s.id} · {s.duration_s != null ? fmtTime(s.duration_s) : "?"} · {s.summary}
              </div>
            </div>
            <div className="right">
              {s.high > 0 && (
                <span className="pill high">
                  <span className="sev high" /> {s.high} privacy
                </span>
              )}
              {s.outcome ? (
                <span className={`pill ${s.outcome}`}>{s.outcome === "pass" ? "Passed" : "Failed"}</span>
              ) : (
                <span className="pill">{s.reviewed ? "In review" : "Not reviewed"}</span>
              )}
            </div>
          </Link>
        ))
      )}
    </main>
  );
}
