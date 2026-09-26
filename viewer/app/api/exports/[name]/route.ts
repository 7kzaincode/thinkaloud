import { createReadStream, promises as fs } from "fs";
import path from "path";
import { Readable } from "stream";
import { DATA_DIR } from "@/lib/sessions";

type Ctx = { params: Promise<{ name: string }> };

export async function GET(_req: Request, { params }: Ctx) {
  const { name } = await params;
  if (!/^thinkaloud-export-[\w.-]+\.zip$/.test(name)) return new Response("not found", { status: 404 });
  const p = path.join(/*turbopackIgnore: true*/ DATA_DIR, "exports", name);
  const st = await fs.stat(p).catch(() => null);
  if (!st) return new Response("not found", { status: 404 });
  return new Response(Readable.toWeb(createReadStream(p)) as ReadableStream, {
    headers: { "Content-Type": "application/zip", "Content-Length": String(st.size), "Content-Disposition": `attachment; filename="${name}"` },
  });
}
