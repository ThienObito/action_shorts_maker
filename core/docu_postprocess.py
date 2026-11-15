"""Reusable post-processing helpers for authorized documentary projects."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

TEXT_BLUR_MODES = ("none", "auto_detect_blur", "top_blur_brand",
                   "bottom_blur_sub", "both_top_bottom_blur")


def detect_text_bands(source: Path) -> tuple[bool, bool]:
    """Conservative visual text-like edge heuristic; always review the result manually.

    This is not OCR and cannot promise removal of a watermark or embedded subtitles.
    """
    import cv2
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ValueError(f"Cannot inspect text bands: {source}")
    votes = [0, 0]
    try:
        length = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1
        for fraction in (.12, .27, .42, .57, .72, .87):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(length * fraction))
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            height = len(gray)
            for index, band in enumerate((gray[:int(height*.16)], gray[int(height*.78):])):
                band = cv2.resize(band, (640, max(50, int(640*band.shape[0]/band.shape[1]))))
                edges = cv2.Canny(band, 90, 180)
                contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                glyphs = sum(1 for contour in contours if
                             2 <= cv2.boundingRect(contour)[2] <= 45 and
                             4 <= cv2.boundingRect(contour)[3] <= 42)
                votes[index] += glyphs >= 26
    finally:
        capture.release()
    return votes[0] >= 4, votes[1] >= 4


def apply_frosted_blur_text_mask(width: int, height: int, mode: str,
                                 strength: int = 88, source_label: str = "v0") -> tuple[str, str]:
    """Return FFmpeg filter_complex fragment (graph, output label), before ASS burn-in."""
    if (width, height) not in ((1920, 1080), (1080, 1920)):
        raise ValueError("Only master 1920x1080 or Shorts 1080x1920 masks supported")
    if mode not in TEXT_BLUR_MODES or mode == "auto_detect_blur":
        raise ValueError("Resolve auto_detect_blur against source frames first")
    if not 0 <= strength <= 100:
        raise ValueError("Blur strength must be 0..100")
    top = mode in ("top_blur_brand", "both_top_bottom_blur")
    bottom = mode in ("bottom_blur_sub", "both_top_bottom_blur")
    if not top and not bottom:
        return "", source_label
    top_h = 135 if width == 1920 else 245
    bottom_y, bottom_h = ((930, 150) if width == 1920 else (1380, 340))
    radius = max(1, round(28 * strength / 88))
    parts = []
    labels = []
    if top:
        labels.append("top")
    if bottom:
        labels.append("bottom")
    parts.append(f"[{source_label}]split={len(labels)+1}[base]" + "".join(f"[{x}src]" for x in labels))
    current = "base"
    for part in labels:
        y, h = (0, top_h) if part == "top" else (bottom_y, bottom_h)
        border = (f",drawbox=x=0:y={h-(2 if width == 1920 else 4)}:w={width}:"
                  f"h={2 if width == 1920 else 4}:color=0x00E5FF@0.95:t=fill") if part == "top" else ""
        parts.append(f"[{part}src]crop={width}:{h}:0:{y},"
                     f"boxblur=luma_radius={radius}:luma_power=6:chroma_radius={max(1,radius//2)}:chroma_power=4,"
                     f"eq=brightness=-0.16:contrast=0.88,"
                     f"drawbox=x=0:y=0:w={width}:h={h}:color=0x0B1320@0.62:t=fill{border}[{part}glass]")
        parts.append(f"[{current}][{part}glass]overlay=0:{y}[{part}out]")
        current = f"{part}out"
    if top:
        caption = "IRONAXIS LAB // TECHNICAL BREAKDOWN" if width == 1920 else "IRONAXIS LAB"
        parts.append(f"[{current}]drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{caption}':"
                     f"fontsize={36 if width == 1920 else 48}:fontcolor=white:borderw=3:bordercolor=black:"
                     f"x=(w-text_w)/2:y={40 if width == 1920 else 78}" +
                     (",drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='US INDUSTRIAL & CNC ARCHIVE':"
                      "fontsize=24:fontcolor=0x00E5FF:borderw=2:bordercolor=black:x=(w-text_w)/2:y=142"
                      if width == 1080 else "") + "[masked]")
        current = "masked"
    return ";".join(parts), current


def normalize_master_loudness(mix_path: Path) -> float | None:
    """One-pass ITU loudness target after all beds are mixed; preserve PCM duration."""
    import shutil
    temp = mix_path.with_name("master_audio_loudnorm_new.wav")
    result = subprocess.run(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-i", str(mix_path),
                             "-af", "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json",
                             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(temp)],
                            check=True, capture_output=True, text=True, timeout=3600)
    temp.replace(mix_path)
    shutil.copy2(mix_path, mix_path.with_name("full_voiceover_mixed.wav"))
    match = re.search(r'"output_i"\s*:\s*"([^"\n]+)"', result.stderr)
    return float(match.group(1)) if match else None


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def build_master_audio_and_srt(audio_files: list[Path], segments: list[dict], project_dir: Path,
                               hook_duration: float = 0.0, chapter_gap: float = 2.0,
                               tail_sec: float = 1.5, max_words: int = 9,
                               niche_mode: str = "engineering_machining", bgm_style: str = "dynamic_multi") -> dict:
    """Build measured master voice, SRT, timeline and ducked procedural BGM on one clock."""
    from pydub import AudioSegment
    import numpy as np
    if not audio_files or len(audio_files) != len(segments):
        raise ValueError("audio_files and segments must be nonempty and have matching lengths")
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    cursor = float(hook_duration)
    voice = AudioSegment.silent(duration=round(hook_duration * 1000), frame_rate=48000).set_channels(2)
    cues: list[str] = []
    timeline = []
    cue_index = 1
    for i, (path, segment) in enumerate(zip(audio_files, segments)):
        clip = AudioSegment.from_file(path).set_frame_rate(48000).set_channels(2)
        start = cursor
        voice += clip
        end = start + len(clip) / 1000
        text_words = re.findall(r"\S+", str(segment.get("narration_en", "")).strip())
        count = max(1, len(text_words))
        for word_start in range(0, count, max_words):
            group = text_words[word_start:word_start + max_words]
            cue_start = start + (len(clip) / 1000) * word_start / count
            cue_end = start + (len(clip) / 1000) * min(count, word_start + len(group)) / count
            cues.append(f"{cue_index}\n{srt_timestamp(cue_start)} --> {srt_timestamp(cue_end)}\n{' '.join(group)}")
            cue_index += 1
        timeline.append({"chapter": segment.get("chapter", i + 1), "start_sec": start,
                         "end_sec": end, "hud_start": start, "hud_end": min(end, start + 4.5)})
        cursor = end + (chapter_gap if i < len(audio_files) - 1 else tail_sec)
        if i < len(audio_files) - 1:
            voice += AudioSegment.silent(duration=round(chapter_gap * 1000), frame_rate=48000).set_channels(2)
    total_ms = round(cursor * 1000)
    voice = voice[:total_ms]
    if len(voice) < total_ms:
        voice += AudioSegment.silent(duration=total_ms - len(voice), frame_rate=48000).set_channels(2)
    # Normalize voice only; silence and chapter gaps remain silent.
    if voice.max_dBFS != float("-inf"):
        voice = voice.apply_gain(-1.5 - voice.max_dBFS)
    voice_path = project_dir / "full_voiceover.wav"
    voice.export(voice_path, format="wav", parameters=["-ar", "48000"])

    from core.bgm_composer import compose_dynamic_bgm
    bgm = compose_dynamic_bgm(project_dir, total_ms, timeline,
                               hook_duration=hook_duration, niche_mode=niche_mode, bgm_style=bgm_style)
    bgm.export(project_dir / "bgm_master.wav", format="wav")
    mixed = bgm.overlay(voice).set_frame_rate(48000).set_channels(2)
    mixed_path = project_dir / "master_audio_mix.wav"
    mixed.export(mixed_path, format="wav", parameters=["-ar", "48000"])
    import shutil
    shutil.copy2(mixed_path, project_dir / "full_voiceover_mixed.wav")
    srt_path = project_dir / "english_narration.srt"
    try:
        aligned = align_srt_to_audio(audio_files, segments,
                                     [item["start_sec"] - hook_duration for item in timeline], max_words=max_words)
        import pysrt
        aligned_srt = pysrt.from_string(aligned)
        if hook_duration:
            for cue in aligned_srt:
                cue.start.ordinal += round(hook_duration * 1000)
                cue.end.ordinal += round(hook_duration * 1000)
        if aligned_srt:
            aligned_srt[0].start = __import__("pysrt").SubRipTime(milliseconds=round(hook_duration * 1000))
        aligned_path = project_dir / "_aligned_subtitles.srt"
        aligned_srt.save(str(aligned_path), encoding="utf-8")
        srt_path.write_text(aligned_path.read_text(encoding="utf-8"), encoding="utf-8")
        aligned_path.unlink(missing_ok=True)
    except Exception:
        srt_path.write_text("\n\n".join(cues) + "\n", encoding="utf-8")
    (project_dir / "english_narration_timeline.json").write_text(
        __import__("json").dumps({"hook_duration": hook_duration, "chapter_gap": chapter_gap,
                                   "body_target_duration": cursor - hook_duration,
                                   "total_master_duration": cursor, "segments": timeline}, indent=2),
        encoding="utf-8")
    return {"voice": voice_path, "mix": mixed_path, "srt": srt_path,
            "body_target_duration": cursor - hook_duration, "total_master_duration": cursor,
            "segments": timeline}


def self_verify_output(project_dir: Path, hook_duration: float = 0.0,
                       require_shorts: bool = True,
                       expected_duration_sec: float | None = None,
                       usable_source_duration_sec: float | None = None) -> dict:
    """Check output presence, stream geometry and master/audio/subtitle timeline bounds."""
    import json
    project_dir = Path(project_dir)
    master = project_dir / "final_us_documentary_1080p60.mp4"
    voice = project_dir / "full_voiceover.wav"
    mixed_audio = project_dir / "master_audio_mix.wav"
    srt = project_dir / "english_narration.srt"
    thumbnail = project_dir / "thumbnail_16x9.jpg"
    required = [master, voice, srt, thumbnail, mixed_audio]
    missing = [str(path) for path in required if not path.is_file()]
    shorts = sorted((project_dir / "shorts").glob("*.mp4")) if (project_dir / "shorts").exists() else []
    if require_shorts and len(shorts) < 3:
        missing.append(f"three Shorts (found {len(shorts)})")
    if missing:
        raise RuntimeError("Output verification missing: " + ", ".join(missing))
    def duration(path: Path) -> float:
        data = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "json", str(path)], check=True, capture_output=True, text=True).stdout)
        return float(data["format"]["duration"])
    master_duration, voice_duration = duration(master), duration(voice)
    from pydub import AudioSegment
    import json as _json
    timeline_file = project_dir / "english_narration_timeline.json"
    timeline_data = _json.loads(timeline_file.read_text(encoding="utf-8")) if timeline_file.is_file() else {}
    spans = timeline_data.get("segments", [])
    first_start = float(spans[0]["start_sec"]) if spans else 0.0
    # First 7.5 seconds are only a BGM-only measurement when a hook actually exists.
    if first_start >= 7.5:
        sample_start, sample_end = 0.0, 7.5
    else:
        gaps = [(float(a["end_sec"]), float(b["start_sec"])) for a,b in zip(spans,spans[1:])]
        suitable = next(((a,b) for a,b in gaps if b-a >= .5), None)
        sample_start, sample_end = suitable if suitable else (0.0, min(7.5, master_duration))
    sample = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(sample_start),
                             "-t", str(sample_end-sample_start), "-i", str(master),
                             "-vn", "-f", "s16le", "-ac", "2", "-ar", "48000", "-"],
                            check=True, capture_output=True).stdout
    values = np.frombuffer(sample, dtype=np.int16).astype(np.float32)
    intro_rms = float(np.sqrt(np.mean(values**2))) if len(values) else 0.0
    intro_bgm_dbfs = 20 * np.log10(max(intro_rms / 32768, 1e-12))
    cues = []
    for block in srt.read_text(encoding="utf-8-sig").strip().split("\n\n"):
        match = re.search(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})", block)
        if match:
            values = [int(x) for x in match.groups()]
            cues.append((values[0]*3600+values[1]*60+values[2]+values[3]/1000,
                         values[4]*3600+values[5]*60+values[6]+values[7]/1000))
    if not cues:
        raise RuntimeError("Subtitle file has no parseable cues")
    # Video is conformed to measured voice duration. Subtitle cue tail may precede
    # the voice tail because ASR omits trailing silence; report it, but do not use
    # it as the master-duration reference.
    delta = abs(master_duration - voice_duration)
    if delta >= 2.5:
        raise RuntimeError(f"Master timeline duration delta {delta:.3f}s exceeds 2.5s")
    if abs(cues[0][0] - hook_duration) > 0.1:
        raise RuntimeError(f"First subtitle starts {cues[0][0]:.3f}s, expected {hook_duration:.3f}s")
    target_delta = (abs(master_duration - expected_duration_sec)
                    if expected_duration_sec is not None else None)
    target_tolerance = (expected_duration_sec * 0.20
                        if expected_duration_sec is not None else None)
    minimum_runtime_sec = min(480, int(usable_source_duration_sec * 0.85)) \
        if usable_source_duration_sec is not None else 480.0
    target_ok = (master_duration >= minimum_runtime_sec and
                 (target_delta is None or target_delta <= target_tolerance))
    errors = []
    settings_path = project_dir / "production_settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    if settings.get("bgm_style", "dynamic_multi") != "none" and intro_bgm_dbfs < -25.0:
        errors.append("BGM missing or too quiet in Intro/Gaps")
    if master_duration < minimum_runtime_sec:
        errors.append(f"Master runtime {master_duration:.3f}s below mid-roll minimum {minimum_runtime_sec:.1f}s")
    if target_delta is not None and target_delta > target_tolerance:
        errors.append(f"Target runtime delta {target_delta:.3f}s exceeds 20% tolerance ({target_tolerance:.3f}s)")
    return {"ok": target_ok and not errors, "expected_duration_sec": expected_duration_sec,
            "target_duration_delta_sec": target_delta,
            "target_duration_tolerance_sec": target_tolerance,
            "minimum_runtime_sec": minimum_runtime_sec,
            "master_duration_sec": master_duration, "voice_duration_sec": voice_duration,
            "last_subtitle_end_sec": cues[-1][1], "timeline_delta_sec": delta,
            "subtitle_tail_delta_sec": voice_duration - cues[-1][1],
            "intro_bgm_dbfs": intro_bgm_dbfs,
            "bgm_sample_window_sec": [sample_start, sample_end],
            "first_subtitle_start_sec": cues[0][0], "thumbnail": str(thumbnail),
            "shorts": [str(path) for path in shorts],
            "errors": errors}


def align_srt_to_audio(audio_files: list[Path], segments: list[dict], offsets: list[float],
                       max_words: int = 9, max_chars: int = 52,
                       model_name: str = "small", device: str = "cpu") -> str:
    """Use Whisper word timestamps on generated narration; group into readable short cues."""
    if not (len(audio_files) == len(segments) == len(offsets)):
        raise ValueError("audio_files, segments, offsets must have matching lengths")
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(model_name, device=device, compute_type="float16" if device == "cuda" else "int8")
    except Exception:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    blocks: list[str] = []
    cue_num = 1
    for path, segment, offset in zip(audio_files, segments, offsets):
        results, _ = model.transcribe(str(path), language="en", word_timestamps=True,
                                      vad_filter=False, beam_size=1)
        timed = [(w.word.strip(), float(w.start), float(w.end))
                 for result in results for w in (result.words or []) if w.word.strip()]
        if not timed:
            raise RuntimeError(f"No word timings found in generated audio: {path}")
        # Transcript-audio alignment matters: reject ASR substitutions by replacing cue
        # spelling with reference-script words while keeping measured word boundaries.
        reference = re.findall(r"\S+", segment["narration_en"])
        if len(reference) == len(timed):
            timed = [(reference[i], timed[i][1], timed[i][2]) for i in range(len(reference))]
        current: list[tuple[str, float, float]] = []
        for word, start, end in timed:
            candidate = current + [(word, start, end)]
            text = " ".join(item[0] for item in candidate).strip()
            sentence_stop = bool(re.search(r"[.!?][\"']?$", word))
            if current and (len(candidate) > max_words or len(text) > max_chars):
                current_text = " ".join(item[0] for item in current).strip()
                blocks.append(f"{cue_num}\n{srt_timestamp(offset + current[0][1])} --> "
                              f"{srt_timestamp(offset + current[-1][2])}\n{current_text}")
                cue_num += 1
                current = [(word, start, end)]
            else:
                current = candidate
            if sentence_stop and current:
                current_text = " ".join(item[0] for item in current).strip()
                blocks.append(f"{cue_num}\n{srt_timestamp(offset + current[0][1])} --> "
                              f"{srt_timestamp(offset + current[-1][2])}\n{current_text}")
                cue_num += 1
                current = []
        if current:
            text = " ".join(item[0] for item in current).strip()
            blocks.append(f"{cue_num}\n{srt_timestamp(offset + current[0][1])} --> "
                          f"{srt_timestamp(offset + current[-1][2])}\n{text}")
            cue_num += 1
    return "\n\n".join(blocks) + "\n"


def choose_sharp_frame(source: Path, output: Path, start_sec: float = 0,
                       end_sec: float | None = None, samples: int = 40) -> float:
    """Sample source frames and save sharpest frame as 16:9 JPEG; return source timestamp."""
    import cv2
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open source video: {source}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 and frame_count else 0
        end = min(duration, end_sec) if end_sec is not None and duration else (end_sec or duration)
        if end <= start_sec:
            raise ValueError("thumbnail sampling range is empty")
        best_score, best_time, best = -1.0, start_sec, None
        for i in range(max(1, samples)):
            timestamp = start_sec + (end - start_sec) * (i + 0.5) / max(1, samples)
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            score = cv2.Laplacian(gray, cv2.CV_64F).var()
            if score > best_score:
                best_score, best_time, best = score, timestamp, frame
        if best is None:
            raise RuntimeError("No readable video frames in thumbnail sampling range")
        height, width = best.shape[:2]
        target_ratio = 16 / 9
        if width / height > target_ratio:
            crop_w = round(height * target_ratio)
            left = (width - crop_w) // 2
            best = best[:, left:left + crop_w]
        else:
            crop_h = round(width / target_ratio)
            top = (height - crop_h) // 2
            best = best[top:top + crop_h, :]
        best = cv2.resize(best, (1280, 720), interpolation=cv2.INTER_LANCZOS4)
        best = cv2.convertScaleAbs(best, alpha=1.08, beta=2)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output), best, [cv2.IMWRITE_JPEG_QUALITY, 94]):
            raise RuntimeError(f"Could not write thumbnail: {output}")
        return best_time
    finally:
        capture.release()
