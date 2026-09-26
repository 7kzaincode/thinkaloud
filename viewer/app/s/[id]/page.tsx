import { notFound } from "next/navigation";
import { getSession } from "@/lib/sessions";
import Reviewer from "./Reviewer";

export const dynamic = "force-dynamic";

export default async function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const id = decodeURIComponent((await params).id);
  const s = await getSession(id);
  if (!s) notFound();
  return <Reviewer id={id} initial={s.trajectory} hadReview={s.reviewed} rebased={s.rebased} />;
}
