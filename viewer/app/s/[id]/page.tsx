import { notFound } from "next/navigation";
import { getSession } from "@/lib/sessions";
import Reviewer from "./Reviewer";

export const dynamic = "force-dynamic";

export default async function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const s = await getSession(decodeURIComponent(id));
  if (!s) notFound();
  return <Reviewer id={decodeURIComponent(id)} initial={s.trajectory} hadReview={s.reviewed} />;
}
