"""Command-line entry point for automated short selection and rendering."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests
from config import (DEFAULT_CLIP, DEFAULT_FRAMING_MODE, DEFAULT_MEME_STYLE,
                    DEFAULT_SFX_DENSITY, DEMO_URL, ENABLE_SFX, FRAMING_MODES,
                    MAX_CLIP, MEME_STYLES, MIN_CLIP, ORIGINAL_AUDIO_VOLUME,
                     OUTPUTS, PRESETS, SFX_DENSITY_MODES, SFX_INTENSITY, DEMO_TITLE,
                     INTRO_SKIP_SECONDS, LONG_VIDEO_THRESHOLD,
                     SHORTS_MODES, AI_MODEL, AI_MODEL_CHOICES, ensure_dirs, font_path)
from core.analyzer import Candidate, find_candidates, probe
from core.ai_director import direct, discover_api_and_model, local_english_metadata, sample_frame, write_seo_us
from core.compiler import compile_crashes
from core.downloader import acquire
from core.meme_fetcher import MEME_DIR, SOUNDS, available_memes, fetch_meme_pack
from core.renderer import render
from core.sfx import ensure_sfx_pack
from core.tracker import bootstrap_model
from core.thumbnail import generate_thumbnails
from config import AI_API_BASE, AI_API_KEY


def main() -> None:
    parser = argparse.ArgumentParser(description="Make tracked 9:16 action shorts from a video.")
    parser.add_argument("source", nargs="?", help="YouTube URL or local video path")
    parser.add_argument("--test-demo", action="store_true", help="Download the McQueen demo and render shorts")
    parser.add_argument("--download-demo", action="store_true", help="Download the demo without rendering")
    parser.add_argument("--bootstrap", action="store_true", help="Prepare directories, font and YOLO weights")
    parser.add_argument("--count", "--max-clips", type=int, default=None, help="Maximum number of clips")
    parser.add_argument("--length", type=float, default=DEFAULT_CLIP, help="Manual clip length in seconds (16–20); automatic clips frame each impact")
    parser.add_argument("--start", type=float, help="Manual clip start time in seconds")
    parser.add_argument("--preset", choices=PRESETS, default="action")
    parser.add_argument("--caption", help="Override comic caption")
    parser.add_argument("--fov-scale", type=float,
                        help="Horizontal field of view (1.0 tight, 2.5 widest; default 1.75)")
    parser.add_argument("--framing-mode", choices=FRAMING_MODES, default=DEFAULT_FRAMING_MODE,
                        help="Camera framing preset (custom --fov-scale overrides its width)")
    sfx_group = parser.add_mutually_exclusive_group()
    sfx_group.add_argument("--sfx", dest="enable_sfx", action="store_true", default=ENABLE_SFX)
    sfx_group.add_argument("--no-sfx", dest="enable_sfx", action="store_false")
    parser.add_argument("--sfx-intensity", type=float, default=SFX_INTENSITY, help="SFX volume multiplier (0–1.5)")
    parser.add_argument("--orig-volume", type=float, default=ORIGINAL_AUDIO_VOLUME,
                        help="Original video audio volume (0–1; default 0.28)")
    parser.add_argument("--sfx-density", choices=SFX_DENSITY_MODES,
                        default=DEFAULT_SFX_DENSITY, help="Kids Hyper-Fun, standard, or off")
    parser.add_argument("--meme-style", choices=MEME_STYLES, default=DEFAULT_MEME_STYLE,
                        help="Real viral memes (default), hybrid, or synthetic only")
    parser.add_argument("--fetch-memes", action="store_true",
                         help="Download / refresh all 40 recorded meme sounds")
    parser.add_argument("--no-ai-director", action="store_true", help="Use local English metadata without API calls")
    parser.add_argument("--ai-model", choices=AI_MODEL_CHOICES, default=AI_MODEL,
                        help="Codex Responses API model")
    parser.add_argument("--ai-assets-only", action="store_true", help="Generate metadata and thumbnails without rerendering video")
    parser.add_argument("--impact-sfx", help="Custom .mp3/.wav in assets/sfx replacing the impact sound")
    parser.add_argument("--shorts-mode", choices=SHORTS_MODES, default="viral")
    parser.add_argument("--desired-duration", type=float, help="Compilation target duration in seconds")
    parser.add_argument("--compile-top-crashes", dest="compile_top_crashes", action="store_true", default=True)
    parser.add_argument("--no-compile-top-crashes", dest="compile_top_crashes", action="store_false")
    args = parser.parse_args()
    if args.fov_scale is None:
        args.fov_scale = FRAMING_MODES[args.framing_mode]["fov_scale"]
    ensure_dirs()
    ensure_sfx_pack()
    if args.fetch_memes:
        fetch_meme_pack(force=True)
        print(f"Real memes: {len(available_memes())}/{len(SOUNDS)} in {MEME_DIR}", flush=True)
        if not (args.source or args.test_demo or args.bootstrap or args.download_demo):
            return
    if args.bootstrap or args.source or args.test_demo:
        print(f"Font: {font_path() if font_path().is_file() else 'Pillow bitmap fallback'}")
        print(f"YOLO model: {bootstrap_model()}")
    if args.download_demo and not (args.source or args.test_demo):
        print(f"Demo video: {acquire(DEMO_URL)}")
        return
    if args.bootstrap and not (args.source or args.test_demo):
        return
    if not (args.source or args.test_demo):
        parser.error("Provide a source, --test-demo, or --bootstrap")
    total_start = time.perf_counter()
    source = acquire(DEMO_URL if args.test_demo else args.source)
    if args.desired_duration and args.desired_duration > 35 and args.shorts_mode == "viral":
        args.shorts_mode = "classic59" if args.desired_duration <= 59 else "extended179"
    compiling = (args.shorts_mode != "viral" and args.compile_top_crashes and args.start is None)
    if args.start is not None:
        if args.start < 0:
            parser.error("--start must be nonnegative")
        duration = probe(source)["duration"]
        clips = [Candidate(args.start, min(max(args.length, MIN_CLIP), MAX_CLIP, duration - args.start),
                           args.start + args.length * 0.65, 0)]
    else:
        count = args.count or (9 if args.shorts_mode == "extended179" else 3)
        clips = find_candidates(source, count=max(1, count), length=args.length)
    for index, clip in enumerate(clips, 1):
        print(f"Selected {index}: source start={clip.start:.3f}s, "
              f"collision={clip.peak:.3f}s, end={clip.start + clip.duration:.3f}s "
              f"(score={clip.score:.3f})", flush=True)
    # Produce upload assets independently from video encoding. API outages
    # degrade to clearly labeled, deterministic US-English local metadata.
    choice = None
    if AI_API_KEY and not args.no_ai_director:
        try:
            choice = discover_api_and_model(AI_API_BASE, AI_API_KEY,
                                            sample_frame(source, clips[0].peak), args.ai_model)
            print(f"AI Director: {choice.model} ({choice.mode})", flush=True)
        except (RuntimeError, ValueError, OSError) as exc:
            print(f"AI Director unavailable ({exc}); using local en-US metadata.", flush=True)

    def asset(segments: list[Candidate], stem: str, compilation: bool = False) -> dict:
        nonlocal_choice = choice
        mode = "local-fallback"
        if nonlocal_choice:
            try:
                data, used_choice = direct(source, segments, title="BeamNG McQueen obstacle-course crashes" if args.test_demo else source.stem,
                                           choice=nonlocal_choice, model=args.ai_model)
                mode = "live-ai"
            except (requests.RequestException, RuntimeError, ValueError, OSError) as exc:
                print(f"AI metadata unavailable ({type(exc).__name__}); using local en-US metadata.", flush=True)
                data = local_english_metadata(compilation=compilation)
        else:
            data = local_english_metadata(compilation=compilation)
        data["director_mode"] = mode
        data["source_segments"] = [{"start_time": c.start, "t_peak": c.peak,
                                     "end_time": round(c.start + c.duration, 3)} for c in segments]
        vertical, horizontal = generate_thumbnails(source, segments[0], OUTPUTS / stem,
                                                  data["thumbnail_text"])
        sidecar = OUTPUTS / f"{stem}.metadata.json"
        sidecar.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        seo = write_seo_us(OUTPUTS / f"{stem}_seo_us.txt", data)
        print(f"Assets: {vertical}, {horizontal}, {sidecar}, {seo} ({mode})", flush=True)
        return data

    clip_assets = {clip.peak: asset([clip],
                                   (f"{source.stem}_{args.shorts_mode}_part_{index:02d}"
                                    if compiling else f"{source.stem}_short_{index:02d}"))
                   for index, clip in enumerate(clips, 1)}
    target = min(float(args.desired_duration or (57 if args.shorts_mode == "classic59" else 171)),
                 SHORTS_MODES[args.shorts_mode]["max_seconds"] - 1)
    desired_parts = min(9, max(2, round(target / 19)))
    parts_count = min(len(clips), desired_parts, args.count or desired_parts)
    compilation_clips = sorted(clips[:parts_count], key=lambda c: c.start)
    part_length = min(25.0, target / parts_count, (SHORTS_MODES[args.shorts_mode]["max_seconds"] - 1) / parts_count)
    min_comp_start = INTRO_SKIP_SECONDS if probe(source)["duration"] > LONG_VIDEO_THRESHOLD else 0.0
    actual_parts = [Candidate(max(min_comp_start, c.peak - (part_length - 4.5)),
                              part_length, c.peak, c.score) for c in compilation_clips]
    compilation_asset = (asset(actual_parts,
                               f"{source.stem}_{args.shorts_mode}_compilation", True)
                         if compiling else None)
    if args.ai_assets_only:
        return
    if compiling:
        destination = OUTPUTS / f"{source.stem}_{args.shorts_mode}_compilation.mp4"
        print(compile_crashes(source, clips, destination, shorts_mode=args.shorts_mode,
                              desired_duration=args.desired_duration, max_clips=args.count,
                              preset=args.preset, caption=args.caption,
                              enable_sfx=args.enable_sfx, sfx_intensity=args.sfx_intensity,
                               impact_sfx=args.impact_sfx, framing_mode=args.framing_mode,
                               fov_scale=args.fov_scale,
                               original_audio_volume=args.orig_volume,
                               sfx_density=args.sfx_density, meme_style=args.meme_style,
                               director_segments=[clip_assets[c.peak] for c in compilation_clips]))
        print(f"Total elapsed (including analysis): {time.perf_counter() - total_start:.2f}s", flush=True)
        return
    for index, clip in enumerate(clips, 1):
        destination = OUTPUTS / f"{source.stem}_short_{index:02d}.mp4"
        print(f"Rendering {index}/{len(clips)}: {clip.start:.1f}s–{clip.start + clip.duration:.1f}s")
        render_start = time.perf_counter()
        print(render(source, clip, destination, args.preset, args.caption,
                     enable_sfx=args.enable_sfx, sfx_intensity=args.sfx_intensity,
                      shorts_mode=args.shorts_mode, impact_sfx=args.impact_sfx,
                      framing_mode=args.framing_mode, fov_scale=args.fov_scale,
                      original_audio_volume=args.orig_volume,
                      sfx_density=args.sfx_density, sfx_variant=index - 1,
                       meme_style=args.meme_style,
                       director_memes=clip_assets[clip.peak]["selected_memes"],
                       hook_text=clip_assets[clip.peak]["hook_text"],
                       impact_text=clip_assets[clip.peak]["impact_text"]))
        print(f"Render elapsed: {time.perf_counter() - render_start:.2f}s", flush=True)
    print(f"Total elapsed (including analysis): {time.perf_counter() - total_start:.2f}s", flush=True)


if __name__ == "__main__":
    main()
