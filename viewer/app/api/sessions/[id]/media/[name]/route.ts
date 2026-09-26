import { createReadStream, promises as fs } from "fs";
import { Readable } from "stream";
import { mediaPath } from "@/lib/sessions";

type Ctx = { params: Promise<{ id: string; name: string }> };

/** Serves playback.mp4 with HTTP Range support so the browser can seek. */
export async function GET(req: Request, { params }: Ctx) {
  const { id, name } = await params;
  const p = await mediaPath(id, name);
  if (!p) return new Response("not found", { status: 404 });
  const size = (await fs.stat(p)).size;
  const range = req.headers.get("range");
  const headers: Record<string, string> = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Cache-Control": "no-cache" };
  if (range) {
    const m = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!m) return new Response("bad range", { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    let start = m[1] ? parseInt(m[1], 10) : size - parseInt(m[2] || "0", 10);
    let end = m[1] && m[2] ? parseInt(m[2], 10) : size - 1;
    if (!m[1]) end = size - 1;
    start = Math.max(0, start);
    end = Math.min(end, size - 1);
    if (start > end) return new Response("bad range", { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    const stream = Readable.toWeb(createReadStream(p, { start, end })) as ReadableStream;
    return new Response(stream, {
      status: 206,
      headers: { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": String(end - start + 1) },
    });
  }
  const stream = Readable.toWeb(createReadStream(p)) as ReadableStream;
  return new Response(stream, { headers: { ...headers, "Content-Length": String(size) } });
}
