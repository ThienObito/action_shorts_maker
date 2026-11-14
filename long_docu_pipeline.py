"""Rights-conscious long-form documentary preparation and narration pipeline.

Only process media you own, licensed media, or media you have permission to use.
This tool does not alter fingerprints or claim that edits avoid Content ID/YPP review.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import yt_dlp
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
from config import ROOT, OUTPUTS
from us_docu_rewriter import rewrite_documentary, format_srt, save_metadata
from core.docu_postprocess import (align_srt_to_audio, choose_sharp_frame,
                                  build_master_audio_and_srt, self_verify_output)

def sanitize_hud_text(text: str, max_chars: int = 42) -> str:
    """Return short ASCII-only HUD text safe for FFmpeg drawtext."""
    text = str(text).replace("→", ">").replace("—", "-").replace("–", "-").replace("•", "/")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    words = text.split()
    result = ""
    for word in words:
        candidate = f"{result} {word}".strip()
        if len(candidate) > max_chars:
            break
        result = candidate
    return result or text[:max_chars]


def _escape_drawtext(text: str) -> str:
    return (text.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
            .replace(",", r"\,").replace("%", r"\%"))


def _run(command: list[str]) -> subprocess.CompletedProcess:
    if Path(command[0]).name.lower() in {"ffmpeg", "ffmpeg.exe"}:
        command = command[:1] + ["-y", "-nostdin", "-hide_banner", "-loglevel", "warning"] + command[1:]
        output_file = next((Path(arg) for arg in reversed(command) if str(arg).lower().endswith(".mp4") and "outputs" in str(arg).lower()), None)
        root = output_file.parent if output_file else ROOT / "outputs"
        root.mkdir(parents=True, exist_ok=True)
        with (root / "ffmpeg_render.log").open("w", encoding="utf-8") as log:
            return subprocess.run(command, check=True, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT, text=True, timeout=300)
    return subprocess.run(command, check=True, capture_output=True, text=True)


def _probe(path: Path) -> dict:
    result = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    return json.loads(result.stdout)


def ensure_royalty_free_bgm(folder: Path) -> Path:
    """Ensure deterministic procedural ambient bed exists; no source-video audio used."""
    assets = ROOT / "assets" / "bgm"
    assets.mkdir(parents=True, exist_ok=True)
    output = assets / "procedural_ambient_documentary.wav"
    if not output.is_file() or output.stat().st_size < 48_000:
        source = ("aevalsrc='0.12*sin(2*PI*55*t)+0.08*sin(2*PI*110*t)+"
                  "0.05*sin(2*PI*82.41*t)+0.04*sin(2*PI*164.81*t)*exp(-mod(t,4))':"
                  "s=48000:c=stereo:d=1800")
        _run(["ffmpeg", "-f", "lavfi", "-i", source,
              "-af", "lowpass=f=450,aecho=0.8:0.88:60:0.4,volume=0.35",
              "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(output)])
    return output


def assemble_and_master_video(local_files: list[Path], script: dict, narration: Path,
                              folder: Path, progress=None,
                              exclude_ranges: list[tuple[float, float]] | None = None,
                              make_intro_hook: bool = False,
                              mute_source_audio: bool = True,
                              speed_up_source: bool = False,
                              subtitle_path: Path | None = None) -> Path:
    """Assemble source video/audio with original narration; retain source framing and provenance."""
    if not local_files:
        raise ValueError("No source media to assemble")
    narration = folder / "master_audio_mix.wav"
    if not narration.is_file():
        raise FileNotFoundError(f"Mixed master audio required: {narration}")
    report = progress or (lambda _message: None)
    report("Probing source and narration durations")
    source_infos = [_probe(path) for path in local_files]
    narr_info = _probe(narration)
    voice_duration = float(narr_info["format"]["duration"])
    source_durations = [float(info["format"]["duration"]) for info in source_infos]
    total_source = sum(source_durations)
    hook_lead = 8.0 if make_intro_hook else 0.0
    # Narration argument contains full master clock, including hook bed/silence.
    body_voice_duration = max(0.1, voice_duration - hook_lead)
    if total_source + 0.25 < body_voice_duration:
        raise ValueError(f"Source runtime ({total_source:.1f}s) shorter than body narration ({body_voice_duration:.1f}s); refusing to freeze/loop footage")

    # Normalize source list to one silent concat input, preserving every input in order.
    source = local_files[0]
    if len(local_files) > 1:
        concat_list = folder / "source_concat.txt"
        concat_list.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in local_files), encoding="utf-8")
        source = folder / "source_concat.mp4"
        _run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-an", "-c:v", "libx264",
              "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", str(source)])
        source_info = _probe(source)
        source_infos = [source_info]
        source_durations = [float(source_info["format"]["duration"])]
        total_source = source_durations[0]
    source_video = next(s for s in source_infos[0]["streams"] if s.get("codec_type") == "video")
    fps = source_video.get("avg_frame_rate") or source_video.get("r_frame_rate") or "30/1"
    source_audio = next((s for s in source_infos[0]["streams"] if s.get("codec_type") == "audio"), None)
    source_duration = source_durations[0]
    ranges = sorted(exclude_ranges or [])
    previous = 0.0
    for start, end in ranges:
        if start < previous or end <= start or end > source_duration:
            raise ValueError("exclude_ranges must be ordered, non-overlapping, and within source duration")
        previous = end
    keep_ranges = []
    cursor = 0.0
    for start, end in ranges:
        if start > cursor:
            keep_ranges.append((cursor, start))
        cursor = end
    if cursor < source_duration:
        keep_ranges.append((cursor, source_duration))
    if not keep_ranges:
        raise ValueError("exclude_ranges remove entire source")
    if ranges:
        pieces = []
        for i, (start, end) in enumerate(keep_ranges, 1):
            piece = folder / f"edit_keep_{i:02d}.mp4"
            _run(["ffmpeg", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end-start:.3f}",
                  "-map", "0", "-c", "copy", str(piece)])
            pieces.append(piece)
        concat_list = folder / "edit_concat.txt"
        concat_list.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in pieces), encoding="utf-8")
        edited = folder / "source_edits_applied.mp4"
        _run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(edited)])
        source = edited
        source_info = _probe(source)
        source_duration = float(source_info["format"]["duration"])
        source_durations = [source_duration]
        source_infos = [source_info]
        total_source = source_duration
    render_srt = subtitle_path or (folder / "english_narration.srt")
    srt_arg = str(render_srt.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'").replace(" ", r"\ ")
    video_filter = (
        "crop=iw*0.86:ih*0.86,"
        f"subtitles='{srt_arg}':force_style='FontName=Arial,FontSize=15,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H60000000,BorderStyle=1,Outline=1.5,Shadow=1,Alignment=2,MarginV=22',"
        "scale=1920:1080:flags=lanczos,"
        "eq=contrast=1.06:saturation=1.08:brightness=0.01,format=yuv420p"
    )
    if speed_up_source and total_source > body_voice_duration:
        speed_factor = total_source / max(body_voice_duration, 0.001)
        video_filter = f"setpts=PTS/{speed_factor:.6f}," + video_filter
    elif speed_up_source and total_source < body_voice_duration:
        speed_factor = max(0.01, total_source / max(body_voice_duration, 0.001))
        video_filter = f"setpts=PTS/{speed_factor:.6f}," + video_filter
    # narration input is already the normalized voice+BGM master mix; source audio is never mapped.
    input_args = ["-i", str(source)]
    output = folder / "final_us_documentary_1080p60.mp4"
    report("Rendering single-pass NVENC master at source frame rate")
    draw_filters = []
    for segment in script.get("segments", []):
        label = sanitize_hud_text(segment.get("on_screen_tech_spec", ""))
        if not label:
            continue
        label = _escape_drawtext(label)
        start = float(segment.get("hud_start", segment.get("narration_start_time", segment.get("start_time", 0)))) - hook_lead
        end = float(segment.get("hud_end", start + 4.5)) - hook_lead
        start, end = max(0.0, start), max(0.0, end)
        draw_filters.append(
            f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{label}':fontcolor=0xFBBF24:fontsize=18:"
            f"box=1:boxcolor=0x111827@0.88:boxborderw=10:x=24:y=24:"
            f"enable='between(t,{start:.2f},{end:.2f})'")
    final_vf = video_filter + ("," + ",".join(draw_filters) if draw_filters else "")
    render_target = folder / "main_without_hook.mp4"
    hook_path = None
    hook_duration = 0.0
    body_render_source = source
    if make_intro_hook:
        bgm_path = ensure_royalty_free_bgm(folder)
        hook_parts = []
        part_length = 8.0 / 3.0
        part_fps = 30
        visual_duration = total_source
        for i, pct in enumerate((0.35, 0.65, 0.88), 1):
            start = max(0.0, min(visual_duration - part_length, visual_duration * pct - part_length / 2))
            part = folder / f"intro_hook_part_{i:02d}.mp4"
            _run(["ffmpeg", "-ss", f"{start:.3f}", "-i", str(source),
                  "-stream_loop", "-1", "-i", str(bgm_path),
                   "-vf", f"crop=iw*0.86:ih*0.86,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                        f"fps={part_fps},"
                        "drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='PRECISION MACHINING':fontcolor=white:fontsize=48:borderw=3:"
                        "bordercolor=black:x=(w-text_w)/2:y=70:enable='lt(t,4)'",
                  "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p",
                  "-r", str(part_fps),
                  "-t", f"{part_length:.6f}", str(part)])
            hook_parts.append(part)
        hook_list = folder / "intro_hook_concat.txt"
        hook_list.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in hook_parts), encoding="utf-8")
        hook_path = folder / "intro_hook.mp4"
        _run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(hook_list), "-c:v", "libx264",
              "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", "-r", str(part_fps),
              "-an", str(hook_path)])
        hook_duration = float(_probe(hook_path)["format"]["duration"])
        render_target = folder / "main_without_hook.mp4"
    body_audio = narration
    hook_audio = None
    if make_intro_hook:
        body_audio = folder / "master_audio_body.wav"
        hook_audio = folder / "master_audio_hook.wav"
        _run(["ffmpeg", "-i", str(narration), "-af", f"atrim=start={hook_lead:.3f},asetpts=PTS-STARTPTS",
              "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(body_audio)])
        _run(["ffmpeg", "-i", str(narration), "-af", f"atrim=duration={hook_lead:.3f},asetpts=PTS-STARTPTS",
              "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(hook_audio)])
    direct_command = ["ffmpeg", "-i", str(body_render_source), "-i", str(body_audio),
                      "-map", "0:v:0", "-map", "1:a:0", "-vf", final_vf,
                      "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "23", "-b:v", "4500k",
                      "-maxrate", "6000k", "-bufsize", "12000k", "-r", fps,
                      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{body_voice_duration:.3f}", "-shortest", "-max_muxing_queue_size", "4096",
                      "-movflags", "+faststart", str(render_target)]
    _run(direct_command)
    silent_visual = folder / "visual_master_silent.mp4"
    silent_vf = "crop=iw*0.86:ih*0.86,scale=1920:1080:flags=lanczos,eq=contrast=1.06:saturation=1.08:brightness=0.01,format=yuv420p"
    if speed_up_source and total_source > body_voice_duration:
        silent_vf = f"setpts=PTS/{total_source / max(body_voice_duration, 0.001):.6f}," + silent_vf
    elif speed_up_source and total_source < body_voice_duration:
        silent_vf = f"setpts=PTS/{max(0.01, total_source / max(body_voice_duration, 0.001)):.6f}," + silent_vf
    _run(["ffmpeg", "-i", str(body_render_source), "-vf", silent_vf,
          "-an", "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "23", "-b:v", "4500k",
          "-maxrate", "6000k", "-bufsize", "12000k", "-r", fps, "-t", f"{body_voice_duration:.3f}",
          "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent_visual)])
    if hook_path:
        # Build one common master timeline; concat normalizes formats and keeps clean audio.
        concat_graph = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,fps=30,format=yuv420p[v0];"
            "[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,fps=30,format=yuv420p[v1];"
            "[v0][v1]concat=n=2:v=1:a=0[v]"
        )
        hook_visual = folder / "visual_with_hook_silent.mp4"
        _run(["ffmpeg", "-i", str(hook_path), "-i", str(silent_visual), "-filter_complex", concat_graph,
               "-map", "[v]", "-an", "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "23",
               "-b:v", "4500k", "-maxrate", "6000k", "-bufsize", "12000k", "-pix_fmt", "yuv420p",
               "-r", "30", "-movflags", "+faststart", str(hook_visual)])
        hook_visual.replace(silent_visual)
        _run(["ffmpeg", "-i", str(silent_visual), "-i", str(narration),
              "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
              "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart", str(output)])
    else:
        _run(["ffmpeg", "-i", str(render_target), "-i", str(narration), "-map", "0:v:0", "-map", "1:a:0",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
              "-shortest", "-movflags", "+faststart", str(output)])
        render_target.unlink(missing_ok=True)
    return output


def _slug(urls: list[str], title: str | None = None) -> str:
    video_id = parse_qs(urlparse(urls[0]).query).get("v", [urlparse(urls[0]).path.strip("/").split("/")[-1]])[0]
    name = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", "-", name.strip()).strip(" .-")
    name = re.sub(r"-+", "-", name)[:100].rstrip(" .-")
    return f"{name}--{video_id}" if name else video_id


def generate_shorts_funnel(master: Path, script: dict, folder: Path,
                           durations: tuple[int, int, int] = (25, 25, 25)) -> list[dict]:
    """Export three captioned portrait cuts from clean silent master and mixed audio."""
    from pysrt import SubRipFile, SubRipTime
    shorts_dir = folder / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    source_srt = SubRipFile.open(str(folder / "english_narration.srt"), encoding="utf-8")
    names = ("short_01_roughing.mp4", "short_02_precision.mp4", "short_03_final_reveal.mp4")
    result = []
    visual = folder / "visual_master_silent.mp4"
    audio_mix = folder / "master_audio_mix.wav"
    voice_mix = folder / "full_voiceover.wav"
    if not visual.is_file():
        raise FileNotFoundError("Shorts require visual_master_silent.mp4")
    if not audio_mix.is_file():
        raise FileNotFoundError(f"Mixed Shorts audio required: {audio_mix}")
    if not voice_mix.is_file():
        voice_mix = audio_mix
    master_duration = min(float(_probe(visual)["format"]["duration"]),
                          float(_probe(audio_mix)["format"]["duration"]))
    cues = sorted(list(source_srt), key=lambda cue: cue.start.ordinal)
    # Pick complete sentence spans lasting 22–29s, nearest 25%, 55%, and 82%.
    candidates = []
    for first_index, first in enumerate(cues):
        for last in cues[first_index:]:
            end_sec = last.end.ordinal / 1000 + 0.4
            duration = end_sec - first.start.ordinal / 1000
            if duration > 29:
                break
            if duration >= 22 and re.search(r"[.!?][\"']?$", last.text.strip()):
                candidates.append((first.start.ordinal / 1000, end_sec))
    if not candidates:
        raise RuntimeError("No complete subtitle sentence spans between 22 and 29 seconds")
    selected = []
    for fraction in (0.25, 0.55, 0.82):
        target = master_duration * fraction
        option = min(candidates, key=lambda pair: abs(pair[0] - target))
        selected.append(option)
        candidates = [pair for pair in candidates
                      if abs(pair[0] - option[0]) > 29 and abs(pair[1] - option[1]) > 29]
    if len(selected) != 3:
        raise RuntimeError("Could not find three non-overlapping sentence-complete Shorts")
    for index, ((start, end), name) in enumerate(zip(selected, names)):
        end = min(master_duration, end)
        if end - start < 22:
            raise RuntimeError(f"Selected Short is too short: {end-start:.3f}s")
        for stale in (shorts_dir / name,
                      shorts_dir / f"{Path(name).stem}_mux.mp4",
                      shorts_dir / f"{Path(name).stem}_cta.mp4"):
            stale.unlink(missing_ok=True)
        offset_ms = round(start*1000)
        clip_srt = SubRipFile()
        for cue in source_srt:
            cue_start, cue_end = cue.start.ordinal, cue.end.ordinal
            if cue_end <= offset_ms or cue_start >= round(end*1000):
                continue
            text_words = cue.text.split()
            parts = [text_words[n:n+6] for n in range(0, len(text_words), 6)]
            span = max(1, cue_end - cue_start)
            for part_index, words in enumerate(parts):
                part_start = cue_start + span * part_index // len(parts)
                part_end = cue_start + span * (part_index + 1) // len(parts)
                new_start = max(0, part_start-offset_ms)
                new_end = max(new_start+100, min(round((end-start)*1000), part_end-offset_ms))
                clip_srt.append(type(cue)(index=len(clip_srt)+1,
                                          start=SubRipTime(milliseconds=new_start),
                                          end=SubRipTime(milliseconds=new_end), text=" ".join(words)))
        ass_path = shorts_dir / (Path(name).stem + ".ass")
        ass_path.write_text(_shorts_srt_to_ass(clip_srt), encoding="utf-8-sig")
        ass_filter_path = str(ass_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'").replace(" ", r"\ ")
        graph = ("[0:v]split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
                 "crop=1080:1920,boxblur=24:8[back];[fg]crop=ih:ih:(iw-ih)/2:0,scale=1080:1080[front];"
                 f"[back][front]overlay=(W-w)/2:(H-h)/2,ass='{ass_filter_path}'[vout]")
        output = shorts_dir / name
        _run(["ffmpeg", "-ss", f"{start:.3f}", "-i", str(visual), "-t", f"{end-start:.3f}",
              "-filter_complex", graph, "-map", "[vout]", "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "24", "-b:v", "2500k",
              "-maxrate", "3500k", "-bufsize", "6000k", "-r", "30", "-pix_fmt", "yuv420p", "-an", str(output)])
        mux_output = output.with_name(output.stem + "_mux.mp4")
        fade_start = max(0, end - start - 0.5)
        import numpy as np
        from pydub import AudioSegment
        start_ms, end_ms = round(start * 1000), round(end * 1000)
        mix_clip = AudioSegment.from_file(audio_mix)[start_ms:end_ms].set_frame_rate(48000).set_channels(2)
        voice_clip = AudioSegment.from_file(voice_mix)[start_ms:end_ms].set_frame_rate(48000).set_channels(2)
        mix_samples = np.asarray(mix_clip.get_array_of_samples(), dtype=np.float32)
        voice_samples = np.asarray(voice_clip.get_array_of_samples(), dtype=np.float32)
        music_samples = mix_samples - voice_samples
        # The shared mix already targets audible -17.5 dBFS under narration;
        # do not re-boost the BGM by the legacy Shorts-only +8 dB.
        short_samples = np.clip(voice_samples + music_samples, -32768, 32767).astype(np.int16)
        short_audio = AudioSegment(short_samples.tobytes(), frame_rate=48000, sample_width=2, channels=2)
        short_audio_path = shorts_dir / (Path(name).stem + "_audio.wav")
        short_audio.export(short_audio_path, format="wav")
        _run(["ffmpeg", "-i", str(output), "-i", str(short_audio_path), "-t", f"{end-start:.3f}",
              "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af",
              f"alimiter=limit=0.95,afade=t=out:st={fade_start:.3f}:d=0.5",
              "-c:a", "aac", "-b:a", "160k", "-shortest", str(mux_output)])
        mux_output.replace(output)
        # CTA is added after subtitles to avoid Windows FFmpeg fontconfig dependency in a complex graph.
        cta_output = output.with_name(output.stem + "_cta.mp4")
        _run(["ffmpeg", "-i", str(output), "-vf",
               "drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='WATCH FULL BREAKDOWN':fontcolor=white:fontsize=24:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-45",
              "-map", "0:v:0", "-map", "0:a:0", "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "24",
              "-c:a", "copy", "-shortest", str(cta_output)])
        cta_output.replace(output)
        short_audio_path.unlink(missing_ok=True)
        info = _probe(output)
        video = next(s for s in info["streams"] if s.get("codec_type") == "video")
        result.append({"path": str(output.resolve()), "duration_sec": float(info["format"]["duration"]),
                       "width": video.get("width"), "height": video.get("height"),
                       "size_bytes": int(info["format"].get("size", 0))})
    return result


def _shorts_srt_to_ass(subtitles) -> str:
    """Serialize generated Shorts cues to fixed 1080x1920 ASS coordinates."""
    def stamp(milliseconds: int) -> str:
        total_cs = max(0, int(milliseconds) // 10)
        hours, remainder = divmod(total_cs, 360_000)
        minutes, remainder = divmod(remainder, 6_000)
        seconds, centiseconds = divmod(remainder, 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"

    rows = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,58,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,60,60,290,1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    for cue in subtitles:
        text = cue.text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")
        rows.append(f"Dialogue: 0,{stamp(cue.start.ordinal)},{stamp(cue.end.ordinal)},Default,,0,0,0,,{text}")
    return "\n".join(rows) + "\n"


def produce_documentary(youtube_urls: list[str], niche_mode: str = "engineering_machining",
                        target_duration_sec: int = 600, progress=None, use_asr: bool = False,
                        clean_outro_sec: float = 0,
                        thumbnail_range: tuple[float, float] | None = None,
                        voice: str = "en-US-ChristopherNeural",
                        exclude_ranges: list[tuple[float, float]] | None = None,
                        make_intro_hook: bool = False, make_shorts: bool = True,
                        existing_project_dir: Path | None = None,
                        script_override: dict | None = None) -> dict:
    if not youtube_urls:
        raise ValueError("youtube_urls must not be empty")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg and ffprobe must be available in PATH")
    video_ids = [parse_qs(urlparse(url).query).get("v", [""])[0] for url in youtube_urls]
    # Reuse explicitly selected existing project or prior ID folder.
    prior = Path(existing_project_dir).resolve() if existing_project_dir else next((path for path in OUTPUTS.iterdir() if path.is_dir()
                  and any(video_id and video_id in path.name for video_id in video_ids)), None)
    folder = prior or (OUTPUTS / _slug(youtube_urls))
    folder.mkdir(parents=True, exist_ok=True)
    def report(message: str) -> None:
        if progress:
            progress(message)
    local_files = []
    metadata = []
    for index, url in enumerate(youtube_urls, 1):
        video_id = parse_qs(urlparse(url).query).get("v", [""])[0]
        cache_candidates = []
        for cache_dir in (folder, ROOT / "downloads", ROOT / "inputs", OUTPUTS):
            if cache_dir.exists():
                cache_candidates.extend(p for p in cache_dir.glob("**/*") if p.is_file()
                                        and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
                                        and (video_id in p.name or (p.parent == folder and p.name.startswith(f"source_{index:02d}."))))
        cached = next(iter(cache_candidates), None)
        report(f"Reusing cached source {index}/{len(youtube_urls)}" if cached else f"Downloading source {index}/{len(youtube_urls)}")
        if cached:
            local_files.append(cached)
            try:
                info = _probe(cached)
                metadata.append({"title": cached.stem, "duration": float(info["format"]["duration"]), "url": url})
            except Exception:
                metadata.append({"title": cached.stem, "duration": None, "url": url})
            continue
        opts = {"format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "merge_output_format": "mp4", "outtmpl": str(folder / f"source_{index:02d}.%(ext)s"),
                "noplaylist": True, "quiet": True, "force_ipv4": True,
                "sleep_interval": 3, "max_sleep_interval": 8,
                "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}}}
        download_errors = []
        info = None
        for attempt in range(1, 4):
            try:
                with yt_dlp.YoutubeDL(opts) as client:
                    info = client.extract_info(url, download=True)
                break
            except Exception as exc:
                download_errors.append(f"attempt {attempt}/3: {type(exc).__name__}: {exc}")
                report(f"WARNING: download failed for URL {url}, attempt {attempt}/3: {exc}")
                if attempt < 3:
                    time.sleep(3 * attempt)
        if info is None:
            raise RuntimeError(f"Download failed after 3 attempts for {url}: " + " | ".join(download_errors))
        metadata.append({"title": info.get("title"), "duration": info.get("duration"), "url": url})
        candidates = [p for p in folder.glob(f"source_{index:02d}.*")
                      if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
        if not candidates:
            raise FileNotFoundError(f"yt-dlp produced no file for source {index}")
        local_files.append(candidates[0])
    (folder / "source_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    title_slug = _slug(youtube_urls, str(metadata[0].get("title") or ""))
    titled_folder = folder if existing_project_dir else OUTPUTS / title_slug
    if titled_folder.resolve() != folder.resolve():
        if titled_folder.exists():
            # Keep any existing project assets; move only missing files from the ID folder.
            for item in folder.iterdir():
                destination = titled_folder / item.name
                if not destination.exists():
                    shutil.move(str(item), str(destination))
            folder = titled_folder
        else:
            folder.rename(titled_folder)
            folder = titled_folder
        local_files = [folder / path.name for path in local_files]
    # No caption web requests by default; industrial/woodworking videos often have no speech.
    notes = []
    keyframes = []
    for idx, source in enumerate(local_files, 1):
        info = _probe(source)
        runtime = float(info["format"]["duration"])
        for pct in (0.1, 0.3, 0.5, 0.7, 0.9):
            image = folder / f"keyframe_{idx:02d}_{int(pct*100):02d}.jpg"
            _run(["ffmpeg", "-y", "-ss", f"{runtime*pct:.3f}", "-i", str(source),
                  "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "4", str(image)])
            keyframes.append(image)
    source_text = ""
    if use_asr:
        report("No captions downloaded; transcribing source audio locally")
        try:
            from faster_whisper import WhisperModel
            asr = WhisperModel("small", device="cuda", compute_type="float16")
        except Exception:
            asr = WhisperModel("small", device="cpu", compute_type="int8")
        asr_text = []
        for source in local_files:
            result_segments, _ = asr.transcribe(str(source), language="vi", vad_filter=True)
            asr_text.append(" ".join(segment.text.strip() for segment in result_segments if segment.text.strip()))
        source_text = "\n".join(asr_text)
    if existing_project_dir:
        cached_script = folder / "script.json"
    source_runtime = sum(float(item.get("duration") or 0) for item in metadata)
    if source_runtime <= 0:
        raise RuntimeError("Could not determine usable source runtime")
    if clean_outro_sec == 0:
        clean_outro_sec = min(12.0, source_runtime * 0.05)
    raw_outro_sec = clean_outro_sec
    if clean_outro_sec < 0 or clean_outro_sec >= source_runtime:
        raise ValueError("clean_outro_sec must be nonnegative and shorter than source runtime")
    exclude_ranges = sorted(exclude_ranges or [])
    last = 0.0
    for start, end in exclude_ranges:
        if start < last or end <= start or end > source_runtime - clean_outro_sec:
            raise ValueError("exclude_ranges must be ordered, non-overlapping, and within source range")
        last = end
    excluded_duration = sum(end - start for start, end in exclude_ranges)
    usable_duration = source_runtime - clean_outro_sec - excluded_duration
    effective_duration = min(int(target_duration_sec), int(usable_duration * 0.96))
    if effective_duration < 60:
        raise ValueError(f"Usable source runtime too short for documentary narration: {usable_duration:.1f}s")
    if existing_project_dir:
        cached_script = folder / "script.json"
        if cached_script.is_file():
            cached_data = json.loads(cached_script.read_text(encoding="utf-8"))
            cached_words = sum(len(str(s.get("narration_en", "")).split())
                               for s in cached_data.get("segments", []))
            if (cached_data.get("segments")
                    and effective_duration * 135 / 60 * 0.80 <= cached_words
                    <= effective_duration * 145 / 60):
                script_override = cached_data
    report(f"Writing original English documentary script for {effective_duration}s target with sampled keyframes")
    title = "; ".join(str(item.get("title") or "") for item in metadata)
    script = script_override or rewrite_documentary(source_text, niche_mode, int(target_duration_sec), keyframes, title,
                                                     usable_source_duration_sec=usable_duration)
    expected_words = effective_duration * 135 / 60
    actual_words = sum(len(str(segment.get("narration_en", "")).split()) for segment in script.get("segments", []))
    if actual_words < expected_words * 0.80:
        raise ValueError(f"Narration too short for requested runtime: {actual_words} words; need at least {expected_words * 0.80:.0f}")
    (folder / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    save_metadata(script, folder)
    report("Generating narration audio")
    import asyncio
    import edge_tts
    async def narrate():
        clips = [folder / f"narration_{index:02d}.mp3" for index in range(1, len(script["segments"]) + 1)]
        # TTS duration varies by voice and punctuation. Conform generated narration
        # to available source time instead of discovering an overrun at video render.
        reserved_timeline_sec = max(0, len(clips) - 1) * 2.0 + 1.5
        clip_budget_sec = max(60.0, usable_duration - reserved_timeline_sec)
        rate_pct = 3
        for attempt in range(4):
            for target, segment in zip(clips, script["segments"]):
                try:
                    await edge_tts.Communicate(segment["narration_en"], voice,
                                               rate=f"+{rate_pct}%").save(str(target))
                except Exception:
                    fallback_voice = "en-US-GuyNeural" if voice != "en-US-GuyNeural" else "en-US-ChristopherNeural"
                    await edge_tts.Communicate(segment["narration_en"], fallback_voice,
                                               rate=f"+{rate_pct}%").save(str(target))
            clip_duration = sum(float(_probe(path)["format"]["duration"]) for path in clips)
            if clip_duration <= clip_budget_sec:
                break
            if attempt == 3:
                raise ValueError(f"Narration remains too long after TTS rate conformance: "
                                 f"{clip_duration:.1f}s clips > {clip_budget_sec:.1f}s source budget")
            next_rate = min(45, max(rate_pct + 3,
                                    math.ceil((rate_pct + 100) * clip_duration / clip_budget_sec - 100) + 2))
            report(f"Narration exceeds source budget ({clip_duration:.1f}s > {clip_budget_sec:.1f}s); "
                   f"resynthesizing at +{next_rate}% speech rate")
            rate_pct = next_rate
        audio_inputs = []
        filters = []
        for idx, (clip, segment) in enumerate(zip(clips, script["segments"])):
            audio_inputs += ["-i", str(clip)]
            delay = max(0, round(float(segment.get("start_time", 0)) * 1000))
            filters.append(f"[{idx}:a]adelay={delay}|{delay},apad=pad_dur=1[v{idx}]")
        mix = "".join(f"[v{idx}]" for idx in range(len(clips)))
        filters.append(f"{mix}amix=inputs={len(clips)}:duration=longest:normalize=0,apad=whole_dur={effective_duration},atrim=duration={effective_duration}[out]")
        subprocess.run(["ffmpeg", "-y", *audio_inputs, "-filter_complex", ";".join(filters),
                        "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k",
                        str(folder / "english_narration.mp3")], check=True, capture_output=True)
    asyncio.run(narrate())
    chapter_audio = [folder / f"narration_{index:02d}.mp3" for index in range(1, len(script["segments"]) + 1)]
    hook_duration = 8.0 if make_intro_hook else 0.0
    timeline = build_master_audio_and_srt(chapter_audio, script["segments"], folder,
                                          hook_duration=hook_duration, niche_mode=niche_mode)
    full_voice = timeline["voice"]
    master_mix = timeline["mix"]
    offsets = [item["start_sec"] for item in timeline["segments"]]
    for segment, timing in zip(script["segments"], timeline["segments"]):
        segment["narration_start_time"] = timing["start_sec"]
        segment["narration_end_time"] = timing["end_sec"]
        segment["hud_start"] = timing["hud_start"]
        segment["hud_end"] = timing["hud_end"]
    # SRT text and cue bounds use same narration text and measured clip duration from shared builder.
    body_duration = timeline["body_target_duration"]
    effective_duration = timeline["total_master_duration"]
    usable_after_trim = max(0.1, usable_duration)
    speed_factor = usable_after_trim / max(body_duration, 0.1)
    # Full-source mapping preferred. Extreme speed remains supported by setpts rather than truncation.
    body_voice = folder / "body_voiceover.wav"
    _run(["ffmpeg", "-i", str(master_mix), "-af", f"atrim=start={hook_duration:.3f},asetpts=PTS-STARTPTS",
          "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(body_voice)])
    render_sources = local_files
    if clean_outro_sec and len(local_files) == 1 and speed_factor <= 2.2:
        trimmed = folder / "source_trimmed_outro.mp4"
        _run(["ffmpeg", "-i", str(local_files[0]), "-t", f"{source_runtime-clean_outro_sec:.3f}",
              "-map", "0", "-c", "copy", str(trimmed)])
        render_sources = [trimmed]
    elif clean_outro_sec and len(local_files) > 1 and speed_factor <= 2.2:
        concat_list = folder / "source_concat_for_trim.txt"
        concat_list.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in local_files), encoding="utf-8")
        combined = folder / "source_combined_for_trim.mp4"
        _run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-an", "-c:v", "libx264",
              "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", str(combined)])
        trimmed = folder / "source_trimmed_outro.mp4"
        _run(["ffmpeg", "-i", str(combined), "-t", f"{source_runtime-clean_outro_sec:.3f}", "-an",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", str(trimmed)])
        render_sources = [trimmed]
    report("Assembling broadcast master")
    render_voice = master_mix
    body_subtitle = folder / "english_narration.srt"
    (folder / "english_narration_body.srt").unlink(missing_ok=True)
    if make_intro_hook:
        import pysrt
        body_subs = pysrt.open(str(body_subtitle), encoding="utf-8")
        for cue in body_subs:
            cue.start.ordinal = max(0, cue.start.ordinal - round(hook_duration * 1000))
            cue.end.ordinal = max(1, cue.end.ordinal - round(hook_duration * 1000))
        body_subtitle = folder / "subtitle_render_clock.srt"
        body_subs.save(str(body_subtitle), encoding="utf-8")
    (folder / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    video = assemble_and_master_video(render_sources, script, render_voice, folder, report,
                                      exclude_ranges=exclude_ranges, make_intro_hook=make_intro_hook,
                                      speed_up_source=True, subtitle_path=body_subtitle)
    if body_subtitle.name == "subtitle_render_clock.srt":
        body_subtitle.unlink(missing_ok=True)
    info = _probe(video)
    video_stream = next(stream for stream in info["streams"] if stream.get("codec_type") == "video")
    duration = float(info["format"]["duration"])
    thumb = folder / "thumbnail_16x9.jpg"
    source_thumb = local_files[0]
    thumbnail_source_duration = float(metadata[0].get("duration") or source_runtime)
    if thumbnail_range:
        range_start, range_end = thumbnail_range
    else:
        # Thumbnail source is first input, not concatenated timeline. Keep sample
        # bounds inside that file, including when project combines multiple videos.
        range_start = 0.65 * thumbnail_source_duration
        range_end = min(0.80 * thumbnail_source_duration,
                        thumbnail_source_duration - min(clean_outro_sec, thumbnail_source_duration * 0.1) - 1)
        if range_end <= range_start:
            range_start, range_end = 0.0, thumbnail_source_duration
    best_frame_time = choose_sharp_frame(source_thumb, thumb, range_start, range_end)
    try:
        from PIL import Image, ImageDraw, ImageFont
        image = Image.open(thumb).convert("RGB")
        draw = ImageDraw.Draw(image)
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf"
        label = str(script.get("us_clickbait_titles", ["ENGINEERING PROCESS"])[0]).upper()
        if len(label) > 38:
            label = " ".join(label.split()[:4])
        font_size = 54
        font = ImageFont.truetype(str(font_path), font_size) if font_path.is_file() else ImageFont.load_default()
        while font_path.is_file() and draw.textbbox((36, 28), label, font=font, stroke_width=3)[2] > image.width - 30 and font_size > 28:
            font_size -= 2
            font = ImageFont.truetype(str(font_path), font_size)
        box = draw.textbbox((36, 28), label, font=font, stroke_width=3)
        draw.rounded_rectangle((box[0]-16, box[1]-12, box[2]+16, box[3]+12), radius=10, fill=(0,0,0))
        draw.text((36, 28), label, font=font, fill=(255, 210, 30), stroke_width=3, stroke_fill=(0,0,0))
        image.save(thumb, quality=92)
    except Exception:
        pass
    report("Master and thumbnail complete")
    shorts = generate_shorts_funnel(video, script, folder) if make_shorts else []
    expected_duration = float(min(int(target_duration_sec), int(usable_duration * 0.96))) + hook_duration
    verification = self_verify_output(folder, hook_duration=hook_duration,
                                      require_shorts=make_shorts,
                                      expected_duration_sec=expected_duration,
                                      usable_source_duration_sec=usable_duration)
    if not verification["ok"]:
        raise RuntimeError("Output self-verification failed: " + json.dumps(verification))
    return {"project_folder": str(folder.resolve()), "script": str((folder / "script.json").resolve()),
            "narration_srt": str((folder / "english_narration.srt").resolve()),
            "narration_audio": str((folder / "english_narration.mp3").resolve()),
            "metadata": str((folder / "us_seo_metadata.md").resolve()),
            "video_export": str(video.resolve()), "thumbnail": str(thumb.resolve()),
            "thumbnail_source_time_sec": best_frame_time,
            "narration_word_aligned": False, "clean_outro_sec": clean_outro_sec,
            "self_verify_output": verification,
            "exclude_ranges": exclude_ranges, "shorts": shorts,
            "video_report": {"width": video_stream.get("width"), "height": video_stream.get("height"),
                             "fps": video_stream.get("r_frame_rate"), "duration_sec": duration,
                             "size_bytes": int(info["format"].get("size", 0))},
            "source_files": [str(p.resolve()) for p in local_files]}
