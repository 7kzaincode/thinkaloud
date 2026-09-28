import { NextResponse } from "next/server";
import { guard } from "@/lib/guard";
import { SPEECH_MODELS, modelDownloaded, setSpeechModel, speechModel } from "@/lib/speech";

export const dynamic = "force-dynamic";

async function state() {
  const current = await speechModel();
  return {
    speech_model: current.id,
    speech_model_source: current.source,
    speech_models: await Promise.all(SPEECH_MODELS.map(async (m) => ({
      id: m.id, label: m.label, download_mb: m.download_mb, note: m.note, downloaded: await modelDownloaded(m.repo),
    }))),
  };
}

export async function GET(req: Request) {
  const denied = guard(req);
  if (denied) return denied;
  return NextResponse.json(await state());
}

export async function PUT(req: Request) {
  const denied = guard(req, { write: true });
  if (denied) return denied;
  const body = await req.json().catch(() => null);
  try {
    if (typeof body?.speech_model === "string") await setSpeechModel(body.speech_model);
  } catch (e) {
    return NextResponse.json({ error: String(e).replace(/^Error: /, "") }, { status: 400 });
  }
  return NextResponse.json(await state());
}
