import { listSessions } from "@/lib/sessions";
import Batch from "./Batch";

export const dynamic = "force-dynamic";

export default async function Home() {
  const sessions = await listSessions();
  return <Batch initial={sessions} desktop={process.env.THINKALOUD_DESKTOP === "1"} />;
}
