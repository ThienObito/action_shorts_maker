"""Interactive Gradio studio for selecting and rendering portrait clips."""
from __future__ import annotations

import re
import json
from pathlib import Path

import gradio as gr

from config import (DEFAULT_CLIP, DEFAULT_FOV_SCALE, DEFAULT_FRAMING_MODE,
                    DEFAULT_MEME_STYLE, DEFAULT_SFX_DENSITY, DEMO_URL, ENABLE_SFX,
                    FRAMING_MODES, MAX_CLIP, MAX_FOV_SCALE, MEME_STYLES,
                    MIN_CLIP, MIN_FOV_SCALE, ORIGINAL_AUDIO_VOLUME, OUTPUTS, PRESETS, RENDER_MODES,
                     SFX_DENSITY_MODES, SFX_INTENSITY, SHORTS_MODES,
                     AI_API_BASE, AI_API_KEY, INTRO_SKIP_SECONDS,
                     LONG_VIDEO_THRESHOLD, AI_MODEL, AI_MODEL_CHOICES)
from core.analyzer import Candidate, find_candidates, probe
from core.ai_director import direct, discover_api_and_model, local_english_metadata, sample_frame, write_seo_us
from core.compiler import compile_crashes
from core.downloader import acquire
from core.meme_fetcher import MEME_DIR, SOUNDS, available_memes, fetch_meme_pack, normalize_audio
from core.renderer import render
from core.sfx import custom_sfx_files, ensure_sfx_pack
from core.thumbnail import generate_thumbnails


def analyze(url: str, upload: str | None, length: float, mode_label: str):
    if not (upload or url.strip()):
        raise gr.Error("Upload a video or enter a YouTube URL.")
    try:
        source = acquire(upload or url)
        mode = next(key for key, item in SHORTS_MODES.items() if item["label"] == mode_label)
        candidates = find_candidates(source, 9 if mode == "extended179" else 5, length)
        options = [(f"{c.start:.1f}–{c.start + c.duration:.1f}s | action score {c.score:.2f}", i)
                   for i, c in enumerate(candidates)]
        info = probe(source)
        return (str(source), candidates, gr.update(choices=options, value=0),
                candidates[0].start, round(candidates[0].duration),
                f"Loaded {source.name} ({info['duration']:.1f}s). Pick a suggested moment or set the start manually.")
    except Exception as exc:
        raise gr.Error(str(exc)) from exc


def choose(index: int, candidates: list[Candidate]):
    if not candidates:
        return 0, DEFAULT_CLIP
    candidate = candidates[int(index)]
    return candidate.start, round(candidate.duration)


def make(source: str, start: float, length: float, preset: str, caption: str,
         resolution: str, mode_label: str, compile_top_crashes: bool,
         enable_sfx: bool, sfx_intensity: float, impact_sfx: str,
          candidates: list[Candidate], framing_label: str, fov_scale: float,
            original_audio_volume: float, sfx_density_label: str, meme_style_label: str,
            ai_model: str = AI_MODEL):
    if not source:
        raise gr.Error("Analyze a source first.")
    try:
        path = Path(source)
        metadata = probe(path)
        mode = next(key for key, item in SHORTS_MODES.items() if item["label"] == mode_label)
        framing_mode = next(key for key, item in FRAMING_MODES.items()
                            if item["label"] == framing_label)
        sfx_density = next(key for key, label in SFX_DENSITY_MODES.items()
                           if label == sfx_density_label)
        meme_style = next(key for key, label in MEME_STYLES.items()
                          if label == meme_style_label)
        if start >= metadata["duration"]:
            raise ValueError("Start time must be before the end of the video.")
        duration = min(length, metadata["duration"] - start)
        width, height, fps = RENDER_MODES[resolution]
        compiling = mode != "viral" and compile_top_crashes
        if compiling:
            target = min(57 if mode == "classic59" else 171, SHORTS_MODES[mode]["max_seconds"] - 1)
            part_count = min(len(candidates), min(9, max(2, round(target / 19))))
            asset_clips = sorted(candidates[:part_count], key=lambda c: c.start)
        else:
            matched = next((c for c in candidates if abs(c.start - start) < 0.1), None)
            clip = Candidate(start, duration,
                             matched.peak if matched else start + duration * 0.64, 0)
            asset_clips = [clip]
        mode_used = "local-fallback"
        directed = local_english_metadata(compilation=compiling)
        if AI_API_KEY:
            try:
                choice = discover_api_and_model(AI_API_BASE, AI_API_KEY,
                                                sample_frame(path, asset_clips[0].peak), ai_model)
                directed, choice = direct(path, asset_clips, title=path.stem, choice=choice, model=ai_model)
                mode_used = "live-ai"
            except Exception as exc:
                print(f"AI Director unavailable ({type(exc).__name__}); using local English metadata.")
        if compiling:
            output = OUTPUTS / f"{path.stem}_{mode}_{height}p{fps}.mp4"
            finished = compile_crashes(path, candidates, output, shorts_mode=mode,
                                       preset=preset, caption=caption or None,
                                       width=width, height=height, fps=fps,
                                       enable_sfx=enable_sfx, sfx_intensity=sfx_intensity,
                                        impact_sfx=impact_sfx if impact_sfx != "None" else None,
                                        framing_mode=framing_mode, fov_scale=fov_scale,
                                        original_audio_volume=original_audio_volume,
                                          sfx_density=sfx_density, meme_style=meme_style,
                                          director_memes=directed["selected_memes"],
                                          hook_text=directed["hook_text"],
                                          impact_text=directed["impact_text"])
        else:
            output = OUTPUTS / f"{path.stem}_{int(start * 1000)}_{preset}_{height}p{fps}.mp4"
            finished = render(path, clip, output, preset, caption if caption.strip() else None,
                              width=width, height=height, fps=fps, enable_sfx=enable_sfx,
                              sfx_intensity=sfx_intensity, shorts_mode=mode,
                               impact_sfx=impact_sfx if impact_sfx != "None" else None,
                               framing_mode=framing_mode, fov_scale=fov_scale,
                               original_audio_volume=original_audio_volume,
                                 sfx_density=sfx_density, meme_style=meme_style,
                                 director_memes=directed["selected_memes"],
                                 hook_text=directed["hook_text"],
                                 impact_text=directed["impact_text"])
        directed["director_mode"] = mode_used
        if compiling:
            part_length = min(25.0, target / part_count,
                              (SHORTS_MODES[mode]["max_seconds"] - 1) / part_count)
            minimum = INTRO_SKIP_SECONDS if metadata["duration"] > LONG_VIDEO_THRESHOLD else 0.0
            segment_bounds = [(max(minimum, c.peak - (part_length - 4.5)), part_length, c.peak)
                              for c in asset_clips]
        else:
            segment_bounds = [(c.start, c.duration, c.peak) for c in asset_clips]
        directed["source_segments"] = [{"start_time": round(s, 3), "t_peak": peak,
                                         "end_time": round(s + d, 3)} for s, d, peak in segment_bounds]
        vertical, horizontal = generate_thumbnails(path, asset_clips[0], finished.with_suffix(""),
                                                   directed["thumbnail_text"])
        sidecar = finished.with_suffix(".metadata.json")
        sidecar.write_text(json.dumps(directed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        seo = write_seo_us(finished.with_name(finished.stem + "_seo_us.txt"), directed)
        return (str(finished), str(finished), str(vertical), str(horizontal), str(sidecar), str(seo),
                f"Saved {finished}; thumbnails: {vertical}, {horizontal}; SEO: {seo} ({mode_used})")
    except Exception as exc:
        raise gr.Error(str(exc)) from exc


def meme_choices() -> list[str]:
    return [p.name for p in sorted(MEME_DIR.glob("*.wav"))]


def refresh_memes():
    try:
        results = fetch_meme_pack(force=True)
        count = len(available_memes())
        return gr.update(choices=meme_choices()), f"Real meme pack: {count}/{len(SOUNDS)} ready. Sources: {MEME_DIR / 'sources.json'}"
    except Exception as exc:
        raise gr.Error(str(exc)) from exc


def preview_meme(name: str) -> str | None:
    path = MEME_DIR / Path(name).name
    return str(path) if name and path.is_file() else None


def upload_meme(uploaded: str | None):
    if not uploaded:
        raise gr.Error("Choose an MP3 or WAV file to upload.")
    path = Path(uploaded)
    if path.suffix.lower() not in {".mp3", ".wav"} or path.stat().st_size > 8_000_000:
        raise gr.Error("Upload a .mp3/.wav file smaller than 8 MB.")
    MEME_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9_-]+", "_", path.stem.lower()).strip("_") or "custom"
    target = MEME_DIR / f"extra_{stem}.wav"
    suffix = 2
    while target.exists():
        target = MEME_DIR / f"extra_{stem}_{suffix}.wav"
        suffix += 1
    try:
        stats = normalize_audio(path, target)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc
    return (gr.update(choices=meme_choices(), value=target.name), str(target),
            f"Added {target.name} ({stats['duration']}s, peak {stats['peak_dbfs']} dBFS).")


ensure_sfx_pack()

with gr.Blocks(title="ActionShortsMaker") as studio:
    gr.Markdown("# ActionShortsMaker\nCreate tracked 9:16 action shorts. Only download or edit videos you have permission to use.")
    source_state = gr.State("")
    clips_state = gr.State([])
    with gr.Row():
        url = gr.Textbox(label="YouTube URL", value=DEMO_URL)
        upload = gr.Video(label="Or upload a local video", sources=["upload"], format=None)
    with gr.Row():
        length = gr.Slider(MIN_CLIP, MAX_CLIP, value=DEFAULT_CLIP, step=1, label="Clip Duration (seconds)")
        analyze_button = gr.Button("Download / Analyze", variant="primary")
    suggestion = gr.Dropdown(label="Suggested action moment", choices=[])
    start = gr.Number(value=0, minimum=0, label="Clip start (seconds) — editable")
    with gr.Row():
        preset = gr.Dropdown(choices=list(PRESETS), value="action", label="Visual preset")
        resolution = gr.Dropdown(choices=list(RENDER_MODES), value="1080x1920 @ 60fps (Full HD)", label="Resolution & FPS")
        caption = gr.Textbox(label="Comic caption (blank = preset default)", placeholder="WAIT FOR IT!")
    mode = gr.Dropdown(choices=[item["label"] for item in SHORTS_MODES.values()],
                       value=SHORTS_MODES["viral"]["label"], label="YouTube Shorts Duration Limit Mode")
    ai_model_choice = gr.Dropdown(choices=list(AI_MODEL_CHOICES), value=AI_MODEL, label="Codex AI Model")
    with gr.Row():
        framing = gr.Dropdown(choices=[item["label"] for item in FRAMING_MODES.values()],
                              value=FRAMING_MODES[DEFAULT_FRAMING_MODE]["label"],
                              label="Camera Framing Mode")
        fov = gr.Slider(MIN_FOV_SCALE, MAX_FOV_SCALE, value=DEFAULT_FOV_SCALE,
                        step=0.05, label="Custom FOV / Zoom-Out")
    compile_toggle = gr.Checkbox(value=True, label="Compile Top Crashes (Classic / Extended)")
    with gr.Row():
        sfx_toggle = gr.Checkbox(value=ENABLE_SFX, label="Enable Kids Meme & Cartoon SFX")
        sfx_volume = gr.Slider(0, 1.5, value=SFX_INTENSITY, step=0.05, label="SFX Volume")
        original_volume = gr.Slider(0, 1, value=ORIGINAL_AUDIO_VOLUME, step=0.01,
                                    label="Original Video Volume")
        sfx_density = gr.Dropdown(choices=list(SFX_DENSITY_MODES.values()),
                                  value=SFX_DENSITY_MODES[DEFAULT_SFX_DENSITY],
                                  label="SFX Density Mode")
        meme_style = gr.Dropdown(choices=list(MEME_STYLES.values()),
                                 value=MEME_STYLES[DEFAULT_MEME_STYLE],
                                 label="Meme Sound Style")
        custom_impact = gr.Dropdown(choices=["None"] + [str(p.relative_to(p.parents[1]))
                                                     if p.parent.name == "real_memes" else p.name
                                                     for p in custom_sfx_files()],
                                    value="None", label="Custom impact SFX in assets/sfx")
        refresh_sfx = gr.Button("Refresh Custom SFX")
    gr.Markdown("Third-party meme sounds may have copyright restrictions. Check usage rights before publishing.")
    with gr.Row():
        fetch_memes_button = gr.Button("Download / Refresh Real Meme Pack")
        meme_selection = gr.Dropdown(choices=meme_choices(), label="Preview meme sound")
        meme_preview = gr.Audio(label="Meme sound preview", interactive=False)
        extra_meme = gr.File(label="Upload extra meme MP3 / WAV", file_types=[".mp3", ".wav"],
                             type="filepath")
        upload_meme_button = gr.Button("Add Uploaded Sound")
    make_button = gr.Button("Render Short", variant="primary")
    preview = gr.Video(label="Preview")
    download = gr.File(label="Download MP4")
    thumbnail_vertical = gr.Image(label="Vertical thumbnail (1080×1920)", type="filepath")
    thumbnail_horizontal = gr.Image(label="Horizontal thumbnail (1280×720)", type="filepath")
    metadata_download = gr.File(label="US English SEO metadata JSON")
    seo_download = gr.File(label="Copy-ready US SEO text")
    status = gr.Textbox(label="Status", interactive=False)
    analyze_button.click(analyze, [url, upload, length, mode], [source_state, clips_state, suggestion, start, length, status])
    suggestion.change(choose, [suggestion, clips_state], [start, length])
    framing.change(lambda label: FRAMING_MODES[next(key for key, item in FRAMING_MODES.items()
                                                   if item["label"] == label)]["fov_scale"],
                   inputs=framing, outputs=fov)
    refresh_sfx.click(lambda: gr.update(choices=["None"] + [str(p.relative_to(p.parents[1]))
                                                           if p.parent.name == "real_memes" else p.name
                                                           for p in custom_sfx_files()]),
                      outputs=custom_impact)
    fetch_memes_button.click(refresh_memes, outputs=[meme_selection, status])
    meme_selection.change(preview_meme, inputs=meme_selection, outputs=meme_preview)
    upload_meme_button.click(upload_meme, inputs=extra_meme,
                              outputs=[meme_selection, meme_preview, status])
    make_button.click(make, [source_state, start, length, preset, caption, resolution, mode,
                              compile_toggle, sfx_toggle, sfx_volume, custom_impact, clips_state,
                                framing, fov, original_volume, sfx_density, meme_style, ai_model_choice],
                       [preview, download, thumbnail_vertical, thumbnail_horizontal, metadata_download, seo_download, status])


if __name__ == "__main__":
    studio.launch()
