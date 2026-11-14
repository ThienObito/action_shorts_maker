"""Compile non-overlapping crash moments into a capped YouTube Short."""
from __future__ import annotations

import math
import subprocess
import tempfile
import time
from pathlib import Path

from config import (DEFAULT_FOV_SCALE, DEFAULT_FRAMING_MODE, DEFAULT_MEME_STYLE,
                    DEFAULT_SFX_DENSITY, ENABLE_SFX, FPS, HEIGHT, INTRO_SKIP_SECONDS,
                    LONG_VIDEO_THRESHOLD, ORIGINAL_AUDIO_VOLUME, OUTRO_SKIP_SECONDS,
                    OUTPUTS, SFX_INTENSITY, SHORTS_MODES, WIDTH)
from core.analyzer import Candidate, probe
from core.renderer import _nvenc_available, render


def compile_crashes(source: Path, candidates: list[Candidate], output: Path | None = None,
                    shorts_mode: str = "classic59", desired_duration: float | None = None,
                    max_clips: int | None = None, preset: str = "action",
                    caption: str | None = None, width: int = WIDTH,
                    height: int = HEIGHT, fps: int = FPS,
                    enable_sfx: bool = ENABLE_SFX, sfx_intensity: float = SFX_INTENSITY,
                    impact_sfx: str | Path | None = None,
                    framing_mode: str = DEFAULT_FRAMING_MODE,
                    fov_scale: float = DEFAULT_FOV_SCALE,
                    original_audio_volume: float = ORIGINAL_AUDIO_VOLUME,
                    sfx_density: str = DEFAULT_SFX_DENSITY,
                     meme_style: str = DEFAULT_MEME_STYLE,
                     director_memes: dict[str, str] | None = None,
                     hook_text: str | None = None, impact_text: str | None = None,
                     director_segments: list[dict] | None = None) -> Path:
    if shorts_mode not in {"classic59", "extended179"}:
        raise ValueError("Compilation requires classic59 or extended179 mode.")
    if not candidates:
        raise ValueError("No crash candidates were found for compilation.")
    ceiling = SHORTS_MODES[shorts_mode]["max_seconds"]
    target = min(float(desired_duration or (57 if shorts_mode == "classic59" else 171)), ceiling - 1)
    if target <= 0:
        raise ValueError("Compilation duration must be positive.")
    desired_parts = min(9, max(2, round(target / 19)))
    parts_count = min(len(candidates), desired_parts, max_clips or desired_parts)
    if parts_count < 2:
        raise ValueError("Compilation needs at least two non-overlapping crash moments.")
    selected = sorted(candidates[:parts_count], key=lambda clip: clip.start)
    source_duration = probe(source)["duration"]
    min_start = INTRO_SKIP_SECONDS if source_duration > LONG_VIDEO_THRESHOLD else 0.0
    max_end = (source_duration - OUTRO_SKIP_SECONDS
               if source_duration > LONG_VIDEO_THRESHOLD else source_duration)
    # Keep >1s of safety under the ceiling even with AAC packet rounding.
    part_length = min(25.0, target / parts_count, (ceiling - 1) / parts_count)
    if part_length < 18:
        raise ValueError("Target is too short to include the full approach and aftermath for every crash.")
    output = output or OUTPUTS / f"{source.stem}_{shorts_mode}_compilation.mp4"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    began = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="actionshorts_compilation_") as folder:
        temp = Path(folder)
        parts = []
        for index, candidate in enumerate(selected):
            # Put the crash near the end: no more than ~4.5s of aftermath.
            start = max(min_start, candidate.peak - (part_length - 4.5))
            if start + part_length > max_end or candidate.peak >= start + part_length:
                raise ValueError("Crash cannot fit in compilation without intro/outro footage.")
            segment = Candidate(start, part_length, candidate.peak, candidate.score)
            print(f"Compilation part {index + 1}: source start={start:.3f}s, "
                  f"collision={candidate.peak:.3f}s, end={start + part_length:.3f}s", flush=True)
            part = temp / f"part{index:02d}.mp4"
            directed = (director_segments[index] if director_segments else {})
            render(source, segment, part, preset, caption, width=width, height=height, fps=fps,
                   enable_sfx=enable_sfx, sfx_intensity=sfx_intensity,
                   shorts_mode=shorts_mode, impact_sfx=impact_sfx,
                   framing_mode=framing_mode, fov_scale=fov_scale,
                   original_audio_volume=original_audio_volume, sfx_density=sfx_density,
                   sfx_variant=index, meme_style=meme_style,
                   director_memes=directed.get("selected_memes", director_memes),
                   hook_text=directed.get("hook_text", hook_text),
                    impact_text=directed.get("impact_text", impact_text),
                    event_times=directed.get("micro_events"))
            parts.append(part)
        has_audio = probe(parts[0])["audio"]
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for part in parts:
            command.extend(["-i", str(part)])
        streams = "".join(f"[{i}:v][{i}:a]" for i in range(len(parts))) if has_audio else "".join(
            f"[{i}:v]" for i in range(len(parts)))
        concat = f"{streams}concat=n={len(parts)}:v=1:a={int(has_audio)}"
        if has_audio:
            concat += "[joined_video][joined_audio];"
        else:
            concat += "[joined_video];"
        concat += f"[joined_video]fps={fps},trim=duration={target},setpts=PTS-STARTPTS[v]"
        if has_audio:
            concat += f";[joined_audio]aresample=async=1,atrim=duration={target},asetpts=PTS-STARTPTS[a]"
        command.extend(["-filter_complex", concat, "-map", "[v]"])
        if has_audio:
            command.extend(["-map", "[a]"])
        else:
            command.append("-an")
        encoder = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22"]
                   if _nvenc_available() else
                   ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-threads", "0"])
        command.extend(encoder + ["-pix_fmt", "yuv420p"])
        if has_audio:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        command.extend(["-frames:v", str(math.floor(target * fps)), "-t", str(ceiling),
                        "-movflags", "+faststart", str(output.resolve())])
        subprocess.run(command, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, close_fds=True)
    actual = probe(output)["duration"]
    if actual > ceiling + 1e-6:
        raise RuntimeError(f"Compilation exceeded its {ceiling}s ceiling: {actual}s")
    print(f"Compilation: {len(parts)} crashes, {actual:.3f}s, "
          f"elapsed {time.perf_counter() - began:.2f}s", flush=True)
    return output
