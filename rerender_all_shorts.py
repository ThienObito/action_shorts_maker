"""Regenerate Shorts for existing output projects without touching long-form masters."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from long_docu_pipeline import _probe, _run, generate_shorts_funnel


def main() -> int:
    outputs = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "outputs"
    summary = []
    for folder in sorted(path for path in outputs.iterdir() if path.is_dir() and path.name != "jobs"):
        required = [folder / "english_narration.srt", folder / "script.json"]
        if any(not path.is_file() for path in required):
            summary.append({"project": folder.name, "updated": 0, "status": "skipped: missing script or subtitles"})
            continue
        visual = folder / "visual_master_silent.mp4"
        voice = folder / "full_voiceover.wav"
        audio_mix = folder / "master_audio_mix.wav"
        source = folder / "source_01.mp4"
        if not visual.is_file():
            if not source.is_file() or not voice.is_file():
                summary.append({"project": folder.name, "updated": 0, "status": "skipped: no clean visual master or source+voice"})
                continue
            source_duration = float(_probe(source)["format"]["duration"])
            target_duration = float(_probe(voice)["format"]["duration"])
            pts_scale = source_duration / max(target_duration, 0.001)
            _run([
                "ffmpeg", "-y", "-i", str(source), "-vf",
                f"setpts=PTS/{pts_scale:.9f},crop=iw*0.86:ih*0.86,scale=1920:1080:flags=lanczos,setsar=1,format=yuv420p",
                "-an", "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "23", "-b:v", "4500k",
                "-maxrate", "6000k", "-bufsize", "12000k", "-r", "30", "-t", f"{target_duration:.3f}",
                "-movflags", "+faststart", str(visual),
            ])
        if not audio_mix.is_file():
            summary.append({"project": folder.name, "updated": 0, "status": "skipped: missing master_audio_mix.wav"})
            continue
        try:
            script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
            shorts = generate_shorts_funnel(visual, script, folder)
            summary.append({"project": folder.name, "updated": len(shorts), "shorts": shorts, "status": "ok"})
        except Exception as exc:
            summary.append({"project": folder.name, "updated": 0, "status": f"error: {exc}"})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(row["status"] == "ok" for row in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
