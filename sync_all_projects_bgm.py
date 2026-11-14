"""Refresh mixed audio and remux existing masters/Shorts without re-encoding long video."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pydub import AudioSegment

from core.bgm_composer import compose_dynamic_bgm, ensure_bgm_library
from core.docu_postprocess import self_verify_output


ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def refresh(folder: Path) -> dict:
    voice_path = folder / "full_voiceover.wav"
    timeline_path = folder / "english_narration_timeline.json"
    master = folder / "final_us_documentary_1080p60.mp4"
    if not all(path.is_file() for path in (voice_path, timeline_path, master)):
        raise FileNotFoundError("Missing dry voice, timeline or existing master")
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    voice = AudioSegment.from_file(voice_path).set_frame_rate(48000).set_channels(2)
    music = compose_dynamic_bgm(folder, len(voice), timeline["segments"],
                                hook_duration=float(timeline.get("hook_duration", 0)))
    mix = music.overlay(voice)
    new_mix = folder / "master_audio_mix_new.wav"
    mix.export(new_mix, format="wav")
    new_master = folder / "final_us_documentary_1080p60_bgm_new.mp4"
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(master), "-i", str(new_mix),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-t", str(len(voice)/1000), "-movflags", "+faststart", str(new_master)])
        if not new_master.is_file() or new_master.stat().st_size < 100000:
            raise RuntimeError("Remuxed master is empty")
        new_mix.replace(folder / "master_audio_mix.wav")
        shutil.copy2(folder / "master_audio_mix.wav", folder / "full_voiceover_mixed.wav")
        new_master.replace(master)
    finally:
        new_mix.unlink(missing_ok=True)
        new_master.unlink(missing_ok=True)
    shorts_dir = folder / "shorts"
    shorts = []
    for short in sorted(shorts_dir.glob("short_0*.mp4")):
        ass = short.with_suffix(".ass")
        if not ass.is_file():
            raise FileNotFoundError(f"Missing ASS for {short}")
        # ASS is unchanged; existing video stream already has the approved caption style.
        # Recover original audio start from the first cue's matching narration text/timestamp
        # via the same selector used in generate_shorts_funnel below.
        shorts.append(short)
    from long_docu_pipeline import generate_shorts_funnel
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    updated = generate_shorts_funnel(folder / "visual_master_silent.mp4", script, folder)
    verification = self_verify_output(folder, hook_duration=float(timeline.get("hook_duration", 0)), require_shorts=True)
    return {"project": folder.name, "shorts_updated": len(updated),
            "intro_bgm_dbfs": verification["intro_bgm_dbfs"], "ok": verification["ok"],
            "errors": verification["errors"]}


def main() -> int:
    from core.bgm_composer import TRACKS
    files = ensure_bgm_library()
    for path in files:
        if path.name in {entry[0] for entry in TRACKS.values()}:
            audio = AudioSegment.from_file(path)
            print(f"TRACK {path.name}: {len(audio)/1000:.1f}s {audio.dBFS:.2f} dBFS", flush=True)
    results = []
    for folder in sorted(OUTPUTS.iterdir()):
        if not folder.is_dir() or folder.name == "jobs":
            continue
        try:
            row = refresh(folder)
        except Exception as exc:
            row = {"project": folder.name, "ok": False, "error": str(exc)}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print(f"TOTAL {sum(row.get('shorts_updated',0) for row in results)} Shorts / {len(results)} projects", flush=True)
    return 0 if all(row["ok"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
