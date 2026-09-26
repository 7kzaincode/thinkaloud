import { promises as fs } from "fs";
import { framePath } from "@/lib/sessions";
import { guard } from "@/lib/guard";

type Ctx = { params: Promise<{ id: string; path: string[] }> };

export async function GET(req: Request, { params }: Ctx) {
  const denied = guard(req);
  if (denied) return denied;
  const { id, path } = await params;
  const p = await framePath(id, path.join("/"));
  if (!p) return new Response("not found", { status: 404 });
  return new Response(new Uint8Array(await fs.readFile(p)), {
    headers: { "Content-Type": "image/png", "Cache-Control": "private, max-age=3600" },
  });
}
