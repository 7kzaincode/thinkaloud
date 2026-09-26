import { createReadStream, promises as fs } from "fs";
import { Readable } from "stream";
import { mediaPath } from "@/lib/sessions";
import { guard } from "@/lib/guard";

type Ctx = { params: Promise<{ id: string; name: string }> };

/** Serves playback.mp4 with HTTP Range support so the browser can seek. */
export async function GET(req: Request, { params }: Ctx) {
  const denied = guard(req);
  if (denied) return denied;
  const { id, name } = await params;
  const p = await mediaPath(id, name);
  if (!p) return new Response("not found", { status: 404 });
  const size = (await fs.stat(p)).size;
  const range = req.headers.get("range");
  const headers: Record<string, string> = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Cache-Control": "no-cache" };
  // RFC 9110: a Range header we can't parse (other unit, multiple ranges, first > last) is ignored
  // and the whole file is sent; a syntactically valid range that starts past the end is a 416.
  const m = range ? /^bytes=(\d*)-(\d*)$/i.exec(range.trim()) : null;
  const valid = !!m && (m[1] !== "" || m[2] !== "") && !(m[1] && m[2] && parseInt(m[2], 10) < parseInt(m[1], 10));
  if (m && valid) {
    let start = m[1] ? parseInt(m[1], 10) : Math.max(0, size - parseInt(m[2], 10));
    const end = m[1] && m[2] ? Math.min(parseInt(m[2], 10), size - 1) : size - 1;
    if (!m[1] && parseInt(m[2], 10) === 0) start = size; // "bytes=-0" selects nothing
    if (start >= size || size === 0) return new Response("range not satisfiable", { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    const stream = Readable.toWeb(createReadStream(p, { start, end })) as ReadableStream;
    return new Response(stream, {
      status: 206,
      headers: { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": String(end - start + 1) },
    });
  }
  const stream = Readable.toWeb(createReadStream(p)) as ReadableStream;
  return new Response(stream, { headers: { ...headers, "Content-Length": String(size) } });
}
