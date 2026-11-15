"""Reconstruct *new* editable stems from available project materials, with provenance.

These derivatives are not the historical stems used for already-published exports.
Frequency filtering does NOT remove vocals/music. Never label it Demucs output.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pysrt

from core.bgm_composer import compose_dynamic_bgm
from long_docu_pipeline import _master_srt_to_ass, _shorts_srt_to_ass


def _duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                            capture_output=True, text=True, check=True, timeout=30)
    return float(result.stdout.strip())


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *args],
                   check=True, capture_output=True, timeout=7200)


def _render_aligned_demucs_sfx(other: Path, output: Path, duration: float, hook: float) -> None:
    _ffmpeg("-i", str(other), "-af",
            f"highpass=f=120,lowpass=f=6500,loudnorm=I=-16:TP=-1.5:LRA=11,"
            f"adelay={round(hook*1000)}:all=1,"
            f"asetpts=PTS-STARTPTS,apad=whole_dur={duration:.3f},"
            f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS",
            "-ar", "48000", "-ac", "2", str(output))


def _has_audio(path: Path) -> bool:
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, timeout=30)
    return result.returncode == 0 and "audio" in result.stdout


def _select_source_audio(folder: Path) -> tuple[Path | None, str]:
    # Legacy edited/concatenated MP4s may have video only. Prefer the most
    # representative source that actually contains audio, not simply first MP4.
    for name in ("source_edits_applied.mp4", "source_trimmed_outro.mp4",
                 "source_combined_for_trim.mp4", "source_concat.mp4"):
        candidate = folder / name
        if candidate.is_file() and _has_audio(candidate):
            return candidate, name
    originals = sorted(folder.glob("source_[0-9][0-9].mp4"))
    originals = [path for path in originals if _has_audio(path)]
    if len(originals) == 1:
        return originals[0], originals[0].name
    if len(originals) > 1:
        combined = folder / "source_ambient_concat.wav"
        if not combined.is_file() or not combined.stat().st_size:
            temporary = folder / "source_ambient_concat.backfill.wav"
            try:
                labels = "".join(f"[{n}:a:0]" for n in range(len(originals)))
                _ffmpeg(*(item for path in originals for item in ("-i", str(path))),
                        "-filter_complex", f"{labels}concat=n={len(originals)}:v=0:a=1[a]",
                        "-map", "[a]", "-ar", "48000", "-ac", "2", str(temporary))
                temporary.replace(combined)
            finally:
                temporary.unlink(missing_ok=True)
        return combined, " + ".join(path.name for path in originals)
    return None, "no source audio"


def ensure_project_stems_complete(folder: Path, extract_sfx: bool = False) -> dict:
    """Idempotently rebuild available sources; report gaps, never invent an absent source.

    Run explicitly on a trusted local project directory. Each output is written to a
    temporary sibling and atomically moved; existing stems are never overwritten.
    """
    folder = folder.resolve()
    prior_path = folder / "studio_backfill.json"
    try:
        prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.is_file() else {}
    except (OSError, ValueError):
        prior = {}
    report: dict = {"project": folder.name, "created": dict(prior.get("created", {})), "missing": [], "errors": {},
                    "updated": datetime.now(timezone.utc).isoformat()}
    if not folder.is_dir():
        raise ValueError("Project directory missing")
    visual = folder / "visual_master_silent.mp4"
    voice = folder / "full_voiceover.wav"
    mix = folder / "master_audio_mix.wav"
    srt = folder / "english_narration.srt"
    captions = folder / "english_narration.ass"
    bgm = folder / "bgm_master.wav"
    sfx = folder / "ambient_bed.wav"
    timeline_file = folder / "english_narration_timeline.json"
    timeline = json.loads(timeline_file.read_text(encoding="utf-8")) if timeline_file.is_file() else {}
    def create(key: str, path: Path, source: str, action, replace_existing: bool = False):
        if path.is_file() and path.stat().st_size and not replace_existing:
            return
        temp = path.with_name(path.stem + ".backfill" + path.suffix)
        try:
            temp.unlink(missing_ok=True)
            action(temp)
            if not temp.is_file() or not temp.stat().st_size:
                raise RuntimeError("no output produced")
            if key == "sfx" and abs(_duration(temp) - duration) > .25:
                raise RuntimeError("Demucs stem does not span the Master timeline")
            temp.replace(path)
            report["created"][key] = {"source": source, "path": path.name}
        except Exception as exc:
            report["errors"][key] = f"{type(exc).__name__}: {str(exc)[-300:]}"
            temp.unlink(missing_ok=True)
    if srt.is_file():
        create("ass", captions, "timed English SRT (not re-transcribed)",
               lambda temp: temp.write_text(_master_srt_to_ass(pysrt.open(str(srt), encoding="utf-8")), encoding="utf-8-sig"))
        # Existing Shorts .ass are left intact; their timing is local to each Short.
        for short in (folder / "shorts").glob("short_*.mp4"):
            subtitle = short.with_suffix(".ass")
            if subtitle.is_file():
                continue
            # Do not pretend the master clock matches a clipped Short without an edit start.
            report["missing"].append(f"{subtitle.relative_to(folder)}: missing Short edit offset")
    duration_file = visual if visual.is_file() else mix if mix.is_file() else voice
    duration = _duration(duration_file) if duration_file.is_file() else 0
    spans = timeline.get("segments", [])
    if duration and voice.is_file():
        # Recreated musical bed, NOT recovery of the historical mix music.
        create("bgm", bgm, "new procedural six-track chapter bed; not historical BGM",
               lambda temp: compose_dynamic_bgm(folder, round(duration * 1000), spans,
                                                  hook_duration=float(timeline.get("hook_duration", 0)),
                                                  bgm_style="dynamic_multi").export(temp, format="wav"))
    # The legacy jobs were made with mute_all; a filtered original is not a safe
    # machine-only stem. Keep SFX absent unless a Demucs four-stem `other` exists.
    # Prefer the retained edited/trimmed source rather than only part one of a
    # multi-video project. Speed alignment is approximate, not a frame-accurate EDL.
    source = None
    source_description = "source_ambient_aligned.wav (original source undocumented)"
    provenance_file = folder / "source_ambient_provenance.json"
    try:
        sfx_provenance = json.loads(provenance_file.read_text(encoding="utf-8")) if provenance_file.is_file() else {}
    except (ValueError, OSError):
        sfx_provenance = {}
    demucs = next((folder / "demucs_4stem").glob("htdemucs/*/other.wav"), None) if (folder / "demucs_4stem").is_dir() else None
    if not demucs and extract_sfx and duration:
        from core.audio_separator import extract_machine_ambient_audio
        try:
            source, source_description = _select_source_audio(folder)
            if source is None:
                raise ValueError("No original source with audio track")
            body = float(timeline.get("body_target_duration") or duration - float(timeline.get("hook_duration", 0)))
            extract_machine_ambient_audio(source, folder, body, float(timeline.get("hook_duration", 0)),
                                          "extract_env_sfx")
            sfx_provenance = {"source": source_description, "alignment": "whole source stretched to voice body; edits approximate",
                              "separation": "htdemucs four-stem other; vocal/music leakage unverified"}
            provenance_file.write_text(json.dumps(sfx_provenance, indent=2), encoding="utf-8")
            demucs = next((folder / "demucs_4stem").glob("htdemucs/*/other.wav"), None)
        except Exception as exc:
            report["errors"]["sfx"] = f"Demucs failed: {type(exc).__name__}: {str(exc)[-300:]}"
    if demucs and duration:
        from core.audio_separator import verify_and_clean_machine_sfx
        cleaned = demucs.with_name("other_cleaned.wav")
        previous_clean = bool(prior.get("sfx_cleaning", {}).get("applied"))
        if not cleaned.is_file() or not previous_clean:
            vocals = demucs.with_name("vocals.wav")
            temporary = demucs.with_name("other_cleaned.backfill.wav")
            try:
                if not vocals.is_file():
                    raise FileNotFoundError("Demucs vocals.wav unavailable for activity-based suppression")
                temporary.unlink(missing_ok=True)
                stats = verify_and_clean_machine_sfx(vocals, demucs, temporary)
                if abs(_duration(temporary) - _duration(demucs)) > .05:
                    raise RuntimeError("Suppressed SFX duration differs from Demucs other")
                temporary.replace(cleaned)
                stats["path"] = cleaned.relative_to(folder).as_posix()
                stats["applied"] = True
                report["sfx_cleaning"] = stats
            except Exception as exc:
                report["errors"]["sfx_cleaning"] = f"{type(exc).__name__}: {str(exc)[-300:]}"
                temporary.unlink(missing_ok=True)
        else:
            report["sfx_cleaning"] = prior["sfx_cleaning"]
        # An older backfill produced an output shorter by exactly the hook lead:
        # adelay advances timestamps, so reset PTS before atrim. Only repair stems
        # recorded as generated by our own backfill, never a user's original stem.
        repair_sfx = (sfx.is_file() and report["created"].get("sfx", {}).get("path") == sfx.name
                      and abs(_duration(sfx) - duration) > .25)
        if report.get("sfx_cleaning", {}).get("applied"):
            recorded_source = sfx_provenance.get("source", "source_ambient_aligned.wav (original source undocumented)")
            generated_sfx = report["created"].get("sfx", {}).get("path") == sfx.name
            create("sfx", sfx, f"Demucs other with measured active-band attenuation from {recorded_source}; "
                   "speed-aligned approximation; speech/music absence not verified",
                   lambda temp: _render_aligned_demucs_sfx(
                       cleaned, temp, duration, float(timeline.get("hook_duration", 0))),
                   replace_existing=repair_sfx or (generated_sfx and not previous_clean))
            if report["created"].get("sfx", {}).get("path") == sfx.name:
                report["created"]["sfx"]["source"] = (
                    f"Demucs other with measured active-band attenuation from {recorded_source}; "
                    "speed-aligned approximation; speech/music absence not verified")
    for key, path in {"voice": voice, "sfx": sfx, "visual": visual, "bgm": bgm,
                      "ass": captions, "mix": mix}.items():
        if not path.is_file() or not path.stat().st_size:
            report["missing"].append(key)
    report["complete"] = not report["missing"] and not report["errors"]
    (folder / "studio_backfill.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse
    from config import OUTPUTS
    parser = argparse.ArgumentParser(description="Backfill recoverable studio stems; preserve original exports")
    parser.add_argument("--sfx", action="store_true", help="Run costly Demucs separation on project source audio")
    parser.add_argument("--project", help="Process one project ID from OUTPUTS")
    args = parser.parse_args()
    if args.project and ((OUTPUTS / args.project).resolve().parent != OUTPUTS.resolve()
                         or not args.project.startswith("IronAxisLab_")):
        parser.error("--project must name a direct IronAxisLab_* child of OUTPUTS")
    projects = [OUTPUTS / args.project] if args.project else sorted(OUTPUTS.glob("IronAxisLab_*"))
    for project in projects:
        if project.is_dir():
            result = ensure_project_stems_complete(project, extract_sfx=args.sfx)
            print(json.dumps(result, ensure_ascii=True), flush=True)
        elif args.project:
            parser.error(f"Project does not exist: {args.project}")
