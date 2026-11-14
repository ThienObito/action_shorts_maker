"""One-pass portrait renderer with sparse tracking and cached comic overlays."""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config import (DEFAULT_FOV_SCALE, DEFAULT_FRAMING_MODE, DEFAULT_MEME_STYLE,
                    DEFAULT_SFX_DENSITY, ENABLE_SFX, FPS, FRAMING_MODES, HEIGHT,
                    MAX_FOV_SCALE, MEME_STYLES, MIN_FOV_SCALE, ORIGINAL_AUDIO_VOLUME,
                    OUTPUTS, PRESETS, SFX_DENSITY_MODES, SFX_INTENSITY, SHORTS_MODES, WIDTH,
                    ensure_dirs, font_path)
from core.analyzer import Candidate, probe
from core.meme_fetcher import SOUNDS, available_memes, fetch_meme_pack
from core.sfx import (REAL_FALLBACKS, director_meme_timeline, ensure_sfx_pack,
                       real_meme_timeline, resolve_custom_effect, physics_meme_timeline)
from core.storyboard import micro_events
from core.tracker import AutoCropper, foreground_height, source_crop_width


def continuous_crop_expression(centers: np.ndarray, fps: int, axis: int,
                               crop_size: int, source_size: int, knots: int = 10) -> str:
    """Piecewise-linear timestamp expression evaluated on *every* FFmpeg frame."""
    indices = np.linspace(0, len(centers) - 1, min(knots, len(centers)), dtype=int)
    indices = np.unique(indices)
    values = np.clip(centers[indices, axis] - crop_size / 2, 0, source_size - crop_size)
    expression = f"{values[0]:.5f}"
    for left, right, a, b in zip(indices[:-1], indices[1:], values[:-1], values[1:]):
        dt = (right - left) / fps
        expression += f"+{(b-a)/dt:.6f}*max(0\\,min(t-{left/fps:.6f}\\,{dt:.6f}))"
    return expression


@lru_cache(maxsize=1)
def _nvenc_available() -> bool:
    """Check hardware, not just the presence of the encoder in FFmpeg's list."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=256x256:r=1", "-frames:v", "1", "-c:v", "h264_nvenc",
          "-f", "null", os.devnull], capture_output=True, timeout=15,
        stdin=subprocess.DEVNULL, close_fds=True)
    return result.returncode == 0


@lru_cache(maxsize=1)
def _ensure_real_memes_once() -> None:
    """Fetch missing real clips on first use, not once for every compilation segment."""
    if len(available_memes()) < len(SOUNDS):
        try:
            fetch_meme_pack()
        except Exception as exc:
            print(f"Meme pack download incomplete ({exc}); using available sounds and fallbacks.", flush=True)


def _overlay(text: str, width: int, height: int, y: int, color: str,
             banner: bool = False) -> tuple[int, int, np.ndarray, np.ndarray]:
    """Rasterize only the bounding box once; return premultiplied BGR and alpha."""
    font_file = font_path()
    size = round((62 if banner else 108) * width / WIDTH)
    font = ImageFont.truetype(str(font_file), size) if font_file.is_file() else ImageFont.load_default()
    image = Image.new("RGBA", (width, round(200 * height / HEIGHT)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=5)
    x = max(4, (width - (bbox[2] - bbox[0])) // 2)
    top = 12
    if banner:
        draw.rounded_rectangle((x - 24, top, min(width - 1, x + bbox[2] - bbox[0] + 24),
                                top + bbox[3] - bbox[1] + 24), radius=18, fill=(0, 0, 0, 190))
    draw.text((x, top), text, font=font, fill=color, stroke_width=5, stroke_fill="#111111")
    box = image.getbbox()
    if box is None:
        blank = np.zeros((1, 1, 3), dtype=np.float32)
        return 0, 0, blank, blank
    tile = np.asarray(image.crop(box))
    alpha = tile[:, :, 3:4].astype(np.float32) / 255.0
    premultiplied = tile[:, :, :3][:, :, ::-1].astype(np.float32) * alpha
    return box[0], y + box[1], premultiplied, 1.0 - alpha


def sfx_timeline(impact_offset: float, duration: float, density: str = DEFAULT_SFX_DENSITY,
                 variant: int = 0) -> list[tuple[str, float, float]]:
    """Return (effect, time, gain) cues, one timeline for each crash segment."""
    if density not in SFX_DENSITY_MODES:
        raise ValueError(f"Unknown SFX density: {density}")
    if density == "off":
        return []
    if density == "standard":
        events = [("police_siren", 0.5, 0.6),
                  ("riser_suspense", max(0.0, impact_offset - 1.6), 0.75),
                  ("cartoon_boom", max(0.0, impact_offset), 1.0),
                  ("wah_wah_trombone", max(0.0, impact_offset + 1.15), 0.8)]
    else:
        events = [("cartoon_whistle_intro", 0.1, 0.7),
                  ("countdown_beeps", 1.2, 0.6),
                  ("police_siren", 3.2, 0.65)]
        if impact_offset > 8.0:
            events.append(("cartoon_scurry", 5.5, 0.7))
        events += [("train_horn_meme", max(2.5, impact_offset - 4.2), 0.75),
                   ("boing_spring" if variant % 2 == 0 else "slip_peel",
                    max(3.5, impact_offset - 2.4), 0.8),
                   ("riser_suspense", max(4.0, impact_offset - 1.6), 0.8),
                   ("cartoon_boom", impact_offset, 1.0),
                   ("metal_crunch_bonk", impact_offset, 0.8),
                   ("funny_horn_honk", impact_offset + 0.45, 0.7),
                   ("wah_wah_trombone", impact_offset + 1.15, 0.8)]
    return [(name, time, gain) for name, time, gain in events if 0 <= time < duration]


def audio_mix_filters(audio: bool, effect_events: list[tuple[Path, float, float]],
                      first_effect_input: int, duration: float,
                      original_audio_volume: float = ORIGINAL_AUDIO_VOLUME,
                      sfx_intensity: float = SFX_INTENSITY) -> str:
    """FFmpeg-only ducked original + unnormalized SFX mix."""
    audio_filters = []
    labels = []
    if audio:
        audio_filters.append("[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                             "equalizer=f=1200:t=q:w=1.2:g=-5,"
                             f"volume={original_audio_volume:.4f}[original]")
        labels.append("[original]")
    for index, (_, event_time, volume) in enumerate(effect_events):
        label = f"fx{index}"
        delay = round(event_time * 1000)
        audio_filters.append(f"[{first_effect_input + index}:a]aresample=44100,"
                             f"aformat=channel_layouts=stereo,volume={volume * sfx_intensity:.4f},"
                             f"adelay={delay}:all=1[{label}]")
        labels.append(f"[{label}]")
    if len(labels) > 1:
        audio_filters.append("".join(labels) +
                             f"amix=inputs={len(labels)}:duration={'first' if audio else 'longest'}:"
                             "normalize=0[mixed]")
        mixed = "[mixed]"
    else:
        mixed = labels[0]
    audio_filters.append(f"{mixed}alimiter=limit=0.84:attack=5:release=50,"
                         "loudnorm=I=-14:TP=-1.5:LRA=11,"
                         f"apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[a]")
    return ";".join(audio_filters)


def _render_native(source: Path, output: Path, start: float, duration: float, peak_local: float,
                   centers: np.ndarray, source_width: int, source_height: int,
                   width: int, height: int, fps: int, style: dict,
                   hook: tuple, impact_overlay: tuple, audio: bool,
                   encoder_options: list[str], clean: bool = False,
                   enable_sfx: bool = ENABLE_SFX, sfx_intensity: float = SFX_INTENSITY,
                   shorts_mode: str = "viral", impact_sfx: str | Path | None = None,
                   framing_mode: str = DEFAULT_FRAMING_MODE,
                   fov_scale: float = DEFAULT_FOV_SCALE,
                   original_audio_volume: float = ORIGINAL_AUDIO_VOLUME,
                   sfx_density: str = DEFAULT_SFX_DENSITY,
                    sfx_variant: int = 0, meme_style: str = DEFAULT_MEME_STYLE,
                     director_memes: dict[str, str] | None = None,
                     event_times: dict | None = None) -> Path:
    """Apply sparse tracking commands, Lanczos scaling and overlays in FFmpeg."""
    with tempfile.TemporaryDirectory(prefix="actionshorts_filters_") as folder:
        temp = Path(folder)
        crop_w = source_crop_width(source_width, source_height, width, height, fov_scale)
        crop_h = source_height - source_height % 2
        x_expr = continuous_crop_expression(centers, fps, 0, crop_w, source_width)
        y_expr = continuous_crop_expression(centers, fps, 1, crop_h, source_height)
        # One damped impact pulse, not independently randomized per frame.
        pulse = (f"{float(style['shake']):.3f}*sin(45*(t-{peak_local:.4f}))*"
                 f"exp(-6*(t-{peak_local:.4f}))*between(t\\,{peak_local:.4f}\\,{peak_local+0.3:.4f})")
        x_expr = f"min(max(({x_expr})+({pulse})\\,0)\\,{source_width-crop_w})"

        def save_overlay(asset: tuple, name: str) -> tuple[int, int]:
            x, y, premultiplied, inverse = asset
            alpha = 1.0 - inverse
            rgb = np.divide(premultiplied[:, :, ::-1], alpha,
                            out=np.zeros_like(premultiplied), where=alpha > 0)
            rgba = np.concatenate((np.clip(rgb, 0, 255).astype(np.uint8),
                                   np.clip(alpha * 255, 0, 255).astype(np.uint8)), axis=2)
            Image.fromarray(rgba, "RGBA").save(temp / name)
            return x, y

        hook_x, hook_y = save_overlay(hook, "hook.png")
        impact_x, impact_y = save_overlay(impact_overlay, "impact.png")
        tracked = (f"[0:v]setpts=PTS-STARTPTS,crop={crop_w}:{crop_h}:"
                   f"x='{x_expr}':y='{y_expr}',fps={fps}")
        if fov_scale > 1.0:
            fg_h = foreground_height(width, crop_h, crop_w)
            bg_w, bg_h = max(2, width // 4 // 2 * 2), max(2, height // 4 // 2 * 2)
            blur_radius = min(20, max(0, bg_w // 4 - 1))
            filters = (tracked + "[tracked];[tracked]split=2[bg_src][fg_src];"
                       f"[bg_src]scale={bg_w}:{bg_h}:force_original_aspect_ratio=increase:"
                       f"flags=fast_bilinear,crop={bg_w}:{bg_h},"
                       f"boxblur={blur_radius}:4,eq=brightness=-0.15:saturation=1.1,"
                       f"scale={width}:{height}:flags=fast_bilinear,setsar=1[backdrop];"
                       # -2 derives an even height from each changing crop width, including impact zoom.
                        f"[fg_src]scale={width}:-2:flags=lanczos,setsar=1[foreground];"
                        f"[backdrop][foreground]overlay=0:(H-h)/2:shortest=1,"
                        f"drawbox=x=0:y=(h-{fg_h})/2:w=iw:h=4:color=white:t=fill,"
                        f"drawbox=x=0:y=(h+{fg_h})/2-4:w=iw:h=4:color=yellow:t=fill[base]")
        else:
            filters = tracked + f",scale={width}:{height}:flags=lanczos,setsar=1[base]"
        filters += (f";[base]eq=saturation=1.22:contrast=1.06:brightness=0.01,"
                    f"unsharp=3:3:0.6,drawbox=x=0:y={height-12}:"
                    f"w=iw:h=12:color=red:t=fill[graded];"
                    f"color=c=yellow:s={width}x12:r={fps}:d={duration:.6f}[progress];"
                    f"[graded][progress]overlay=x='-w+w*t/{duration:.6f}':y=H-12:"
                    f"shortest=1[polished]")
        if not clean:
            filters += (f";[polished][1:v]overlay={hook_x}:{hook_y}:"
                        "enable='lt(t,3)':eof_action=repeat[hooked];"
                        f"[hooked][2:v]overlay={impact_x}:{impact_y}:"
                        f"enable='between(t,{max(0, peak_local - 1.15):.3f},{peak_local + 1.15:.3f})'"
                        ":eof_action=repeat,setsar=1[v]")
        else:
            filters += ";[polished]setsar=1[v]"
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-ss", str(start), "-t", str(duration), "-i", str(source)]
        if not clean:
            command.extend(["-loop", "1", "-framerate", "1", "-i", "hook.png",
                            "-loop", "1", "-framerate", "1", "-i", "impact.png"])
        effects = ensure_sfx_pack() if enable_sfx and sfx_density != "off" else {}
        effect_events = []
        if effects:
            custom_boom = resolve_custom_effect(impact_sfx)
            if meme_style == "synthetic":
                cues = sfx_timeline(peak_local, duration, sfx_density, sfx_variant)
                meme_files = {}
            else:
                meme_files = available_memes()
                cues = (director_meme_timeline(director_memes, peak_local, duration, sfx_density, event_times)
                        if director_memes else [])
                if event_times:
                    cues += physics_meme_timeline(event_times, duration, sfx_variant)
                elif not director_memes:
                    cues = real_meme_timeline(peak_local, duration, sfx_variant, sfx_density)
                if meme_style == "hybrid" and sfx_density == "hyper":
                    cues += [("countdown_beeps", 1.2, 0.45),
                             ("riser_suspense", max(0, peak_local - 1.6), 0.5)]
            for name, at, gain in cues:
                if at >= duration:
                    continue
                path = meme_files.get(name) or effects.get(name) or effects.get(REAL_FALLBACKS.get(name, ""))
                if path is None:
                    continue
                if name in {"vine_boom", "cartoon_boom"} and custom_boom:
                    path = custom_boom
                effect_events.append((path, at, gain))
            for path, _, _ in effect_events:
                command.extend(["-i", str(path)])
        if audio or effect_events:
            first_effect_input = 1 if clean else 3
            filters += ";" + audio_mix_filters(audio, effect_events, first_effect_input,
                                                duration, original_audio_volume, sfx_intensity)
        command.extend(["-filter_complex", filters, "-map", "[v]"])
        if audio or effect_events:
            command.extend(["-map", "[a]"])
        command.extend(encoder_options + ["-threads", "6", "-fps_mode", "cfr", "-r", str(fps),
                                          "-pix_fmt", "yuv420p"])
        if audio or effect_events:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            command.append("-an")
        cap = SHORTS_MODES[shorts_mode]["max_seconds"]
        command.extend(["-frames:v", str(round(duration * fps)), "-t", str(cap),
                        "-movflags", "+faststart", str(output.resolve())])
        subprocess.run(command, cwd=temp, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, close_fds=True)
        actual_duration = probe(output)["duration"]
        if actual_duration > cap + 1e-6:
            raise RuntimeError(f"Short exceeded its {cap}s ceiling: {actual_duration}s")
    return output


def render(source: Path, clip: Candidate, output: Path | None = None,
           preset: str = "action", caption: str | None = None,
           width: int = WIDTH, height: int = HEIGHT, fps: int = FPS,
           enable_sfx: bool = ENABLE_SFX, sfx_intensity: float = SFX_INTENSITY,
           shorts_mode: str = "viral", impact_sfx: str | Path | None = None,
           framing_mode: str = DEFAULT_FRAMING_MODE,
           fov_scale: float = DEFAULT_FOV_SCALE,
           original_audio_volume: float = ORIGINAL_AUDIO_VOLUME,
           sfx_density: str = DEFAULT_SFX_DENSITY,
           sfx_variant: int = 0, meme_style: str = DEFAULT_MEME_STYLE,
           director_memes: dict[str, str] | None = None,
            hook_text: str | None = None, impact_text: str | None = None,
            event_times: dict | None = None) -> Path:
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset: {preset}")
    if shorts_mode not in SHORTS_MODES:
        raise ValueError(f"Unknown Shorts duration mode: {shorts_mode}")
    if not 0 <= sfx_intensity <= 1.5:
        raise ValueError("SFX intensity must be between 0 and 1.5.")
    if not math.isfinite(original_audio_volume) or not 0 <= original_audio_volume <= 1:
        raise ValueError("Original audio volume must be between 0 and 1.")
    if sfx_density not in SFX_DENSITY_MODES:
        raise ValueError(f"Unknown SFX density mode: {sfx_density}")
    if meme_style not in MEME_STYLES:
        raise ValueError(f"Unknown meme style: {meme_style}")
    if enable_sfx and sfx_density != "off" and meme_style != "synthetic":
        _ensure_real_memes_once()
        if len(available_memes()) < len(SOUNDS):
            print("Real meme pack incomplete; using synthetic fallbacks for missing cues.", flush=True)
    if framing_mode not in FRAMING_MODES:
        raise ValueError(f"Unknown camera framing mode: {framing_mode}")
    if not math.isfinite(fov_scale) or not MIN_FOV_SCALE <= fov_scale <= MAX_FOV_SCALE:
        raise ValueError(f"FOV scale must be between {MIN_FOV_SCALE} and {MAX_FOV_SCALE}.")
    metadata = probe(source)
    start = max(0.0, min(float(clip.start), metadata["duration"]))
    cap_seconds = SHORTS_MODES[shorts_mode]["max_seconds"]
    frame_limit = math.floor(min(float(clip.duration), metadata["duration"] - start,
                                 cap_seconds) * fps + 1e-7)
    duration = frame_limit / fps
    if duration <= 0:
        raise ValueError("Clip starts at or beyond the end of the source video.")
    ensure_dirs()
    output = Path(output) if output else OUTPUTS / f"short_{int(start * 1000):08d}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    style = PRESETS[preset]
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {source}")
    source_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    tracker = AutoCropper(source_width, source_height, output_width=width,
                          output_height=height, fov_scale=fov_scale)
    tracking_start = time.perf_counter()
    centers = tracker.plan(source, start, duration, fps)
    print(f"Tracking: {time.perf_counter() - tracking_start:.1f}s for {len(centers)} frames", flush=True)
    base_crop_width = source_crop_width(source_width, source_height, width, height, fov_scale)
    top_bar = (height - foreground_height(width, source_height, base_crop_width)) // 2
    # Keep captions in the top blurred bar; for legacy fullscreen use the former position.
    caption_y = max(0, (top_bar - round(200 * height / HEIGHT)) // 2) if top_bar else round(140 * height / HEIGHT)
    hook = _overlay(hook_text or "WAIT FOR IT...", width, height, caption_y, "#ffed38", banner=True)
    impact_assets = {
        "BOOM!": _overlay("BOOM!", width, height, caption_y, "#ffed38"),
        "CRASH!": _overlay("CRASH!", width, height, caption_y, "#ff6542"),
    }
    impact_text = (caption or impact_text or "").strip()[:90] or ("CRASH!" if preset == "comic" else "BOOM!")
    impact_overlay = impact_assets.get(impact_text) or _overlay(
        impact_text, width, height, caption_y, style["accent"])
    peak_local = clip.peak - start
    if event_times is None and enable_sfx and sfx_density != 'off' and meme_style != 'synthetic':
        event_times = micro_events(source, Candidate(start, duration, clip.peak, clip.score))
    encoder_options = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22"]
                       if _nvenc_available() else
                        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"])
    print(f"Encoder: {encoder_options[1]}", flush=True)
    return _render_native(source.resolve(), output, start, duration, peak_local,
                          centers, source_width, source_height, width, height, fps,
                          style, hook, impact_overlay, metadata["audio"], encoder_options,
                          clean=preset == "clean", enable_sfx=enable_sfx,
                          sfx_intensity=sfx_intensity, shorts_mode=shorts_mode,
                           impact_sfx=impact_sfx, framing_mode=framing_mode,
                           fov_scale=fov_scale, original_audio_volume=original_audio_volume,
                           sfx_density=sfx_density, sfx_variant=sfx_variant,
                             meme_style=meme_style, director_memes=director_memes,
                             event_times=event_times)
