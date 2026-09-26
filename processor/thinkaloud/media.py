"""playback.mp4: the screen video and the narration on one timeline.

Timeline contract: media time 0 == recording time 0 (the recorder's t0), in
seconds. Video packets are copied from screen.mkv unchanged; their PTS are the
capture midpoints on the recording clock, so a dropped or late frame leaves a
gap rather than shifting later frames. Audio sample 0 was captured at
meta.audio.offset_s on the recording clock, so the track is preceded by that
much silence (or trimmed, if the estimate is negative). Clock drift of the sound
card relative to perf_counter is reported, not corrected; at the measured tens
of ppm it stays in the low milliseconds over a multi-minute recording.

Encoders come from PyAV's bundled FFmpeg (declared in processor/requirements.txt):
video is stream-copied (no re-encode), audio uses FFmpeg's native "aac" encoder.
"""
from __future__ import annotations

import os
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np

PLAYBACK = "playback.mp4"


def build_playback(session: Path, meta: dict, log=print) -> dict:
    video_meta = meta.get("video") or {}
    audio_meta = meta.get("audio") or {}
    video = session / "screen.mkv"
    audio = session / "audio.wav"
    has_v = video.exists() and bool(video_meta.get("frames"))
    offset = audio_meta.get("offset_s")
    has_a = audio.exists() and audio.stat().st_size > 44
    if has_a and offset is None:
        offset = 0.0  # recorder 0.1 measured when stream.start() returned; best available
    info = {"file": None, "timeline": "media time = recording time (s)", "video": has_v,
            "audio": has_a, "audio_offset_s": offset if has_a else None,
            "audio_clock_drift_ppm": audio_meta.get("clock_drift_ppm"),
            "video_frames": video_meta.get("frames"), "video_dropped": video_meta.get("dropped"),
            "status": "ok", "error": None}
    drift = audio_meta.get("clock_drift_ppm")
    if drift is not None and meta.get("duration_s"):
        info["audio_max_drift_ms"] = round(abs(drift) * 1e-6 * meta["duration_s"] * 1000, 2)
    if not (has_v or has_a):
        info.update(status="unavailable", error="no screen video or audio was recorded")
        return info
    out = session / PLAYBACK
    tmp = session / (PLAYBACK + ".tmp")
    errors = []
    for copy_video in (True, False):  # stream copy first; re-encode if the input won't remux
        try:
            _write(tmp, video if has_v else None, audio if has_a else None, offset, copy_video, info)
            os.replace(tmp, out)
            info["file"] = PLAYBACK
            info["bytes"] = out.stat().st_size
            info["video_mode"] = ("copy" if copy_video else "reencoded") if has_v else None
            if errors:
                info["note"] = f"stream copy failed ({errors[0]}); video was re-encoded"
            return info
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
            if tmp.exists():
                tmp.unlink()
            if not has_v:
                break
    info.update(status="failed", error="; ".join(errors))
    log(f"[warn] playback.mp4 not built: {info['error']}")
    return info


def _write(tmp: Path, video: Path | None, audio: Path | None, offset: float | None,
           copy_video: bool, info: dict) -> None:
    import av

    oc = av.open(str(tmp), "w", format="mp4", options={"movflags": "+faststart"})
    try:
        if video is not None:
            ic = av.open(str(video))
            ivs = ic.streams.video[0]
            if copy_video:
                ovs = oc.add_stream_from_template(ivs)
            else:
                ovs = oc.add_stream("libx264", rate=4)
                ovs.width, ovs.height, ovs.pix_fmt = ivs.codec_context.width, ivs.codec_context.height, "yuv420p"
                ovs.codec_context.time_base = Fraction(1, 1000)
                ovs.options = {"preset": "veryfast", "crf": "24", "bf": "0"}
        if audio is not None:
            oas = oc.add_stream("aac", rate=16000, layout="mono")
            oas.bit_rate = 48000
        if video is not None:
            first_pts = None
            if copy_video:
                for packet in ic.demux(ivs):
                    if packet.size == 0 or packet.pts is None:
                        continue  # demuxer flush packet
                    if packet.dts is None:
                        packet.dts = packet.pts  # Matroska omits dts; valid without B-frames
                    if first_pts is None:
                        first_pts = float(packet.pts * packet.time_base)
                    packet.stream = ovs
                    oc.mux(packet)
            else:
                last = -1
                for frame in ic.decode(ivs):
                    ms = int(round(frame.pts * frame.time_base * 1000))
                    ms = max(ms, last + 1)
                    last = ms
                    if first_pts is None:
                        first_pts = ms / 1000
                    frame.pts, frame.time_base = ms, Fraction(1, 1000)
                    for p in ovs.encode(frame):
                        oc.mux(p)
                for p in ovs.encode(None):
                    oc.mux(p)
            ic.close()
            info["video_first_pts_s"] = first_pts
        if audio is not None:
            with wave.open(str(audio), "rb") as w:
                sr = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            shift = int(round((offset or 0.0) * sr))
            pcm = np.concatenate([np.zeros(shift, np.int16), pcm]) if shift >= 0 else pcm[-shift:]
            block = 1024
            for i in range(0, len(pcm), block):
                chunk = pcm[i:i + block]
                frame = av.AudioFrame.from_ndarray(chunk[np.newaxis, :], format="s16", layout="mono")
                frame.sample_rate = sr
                frame.pts, frame.time_base = i, Fraction(1, sr)
                for p in oas.encode(frame):
                    oc.mux(p)
            for p in oas.encode(None):
                oc.mux(p)
            info["audio_duration_s"] = round(len(pcm) / sr, 3)
    finally:
        oc.close()
