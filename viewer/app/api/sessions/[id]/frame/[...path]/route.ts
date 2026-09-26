import { promises as fs } from "fs";
import { framePath } from "@/lib/sessions";

type Ctx = { params: Promise<{ id: string; path: string[] }> };

export async function GET(_req: Request, { params }: Ctx) {
  const { id, path } = await params;
  const p = await framePath(id, path.join("/"));
  if (!p) return new Response("not found", { status: 404 });
  return new Response(new Uint8Array(await fs.readFile(p)), {
    headers: { "Content-Type": "image/png", "Cache-Control": "private, max-age=3600" },
  });
}
