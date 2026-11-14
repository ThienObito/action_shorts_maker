"""Bounded two-video studio pipeline shared by CLI and MCP."""
from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import requests

from config import AI_API_BASE, AI_API_KEY, AI_MODEL, DEMO_TITLE, DEMO_URL, OUTPUTS
from core.ai_director import (APIChoice, _client, _payload, _post, _response_text,
                              direct, local_english_metadata, write_seo_us)
from core.analyzer import Candidate, find_candidates, probe
from core.compiler import compile_crashes
from core.downloader import acquire
from core.meme_fetcher import SOUNDS
from core.project_organizer import project_folder
from core.renderer import render
from core.resource_manager import heavy_job, worker_pool
from core.sfx import ensure_sfx_pack
from core.storyboard import contact_sheet, micro_events
from core.thumbnail import generate_thumbnails

EXPERT_ENDPOINT = 'https://token.vietshare.site/cdx/responses'


def _title(url: str, source: Path) -> str:
    if url == DEMO_URL or source.stem == '-brcT3E5Dcg':
        return DEMO_TITLE
    if Path(url).is_file():
        return Path(url).stem
    import yt_dlp
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True,
                           'noplaylist': True, 'skip_download': True}) as client:
        return client.extract_info(url, download=False).get('title', source.stem)


def _candidates(source: Path, minimum: int) -> list[Candidate]:
    return find_candidates(source, count=max(3, minimum))


def _storyboards(source: Path, clips: list[Candidate], folder: Path) -> list[dict]:
    plans = []
    for index, clip in enumerate(clips, 1):
        sheet = contact_sheet(source, clip, folder / f'storyboard_{index:02d}.jpg')
        events = micro_events(source, clip)
        plans.append({'candidate': index, 'start': clip.start, 'duration': clip.duration,
                      'peak': clip.peak, 'rank_score': clip.score,
                      'contact_sheet': str(sheet.resolve()), 'telemetry': events})
    return plans


def expert_report(title: str, plans: list[dict], sheet: Path) -> tuple[str, str]:
    prompt = ("You are a Senior US Kids Shorts Editor. Output a detailed Markdown report, NOT "
              "JSON. Analyze the provided candidate-1 contact sheet and measured candidate "
              "telemetry; for other candidates without images, state visual verification is "
              "required rather than inventing observations. Include six-shot storyboards, "
              "cut in/out EDL timestamps, physics-driven SFX choices and reasons, camera "
              "motion, thumbnail concept, and false-positive checks. Available SFX filenames: "
              f"{', '.join(name+'.wav' for name in SOUNDS)}. "
              f"Source title (not scene evidence): {title}. "
              f"Telemetry: {json.dumps(plans, ensure_ascii=True)}")
    if not AI_API_KEY:
        return '# Expert report unavailable\n\nNo API key configured.\n', 'local-fallback'
    try:
        with _client(AI_API_KEY) as client:
            for endpoint in (EXPERT_ENDPOINT, AI_API_BASE+'/responses'):
                try:
                    response = _post(client, endpoint,
                                     _payload(AI_MODEL, endpoint, prompt, sheet.read_bytes()))
                except (requests.HTTPError, requests.Timeout):
                    response = _post(client, endpoint, _payload(AI_MODEL, endpoint, prompt))
                text = _response_text(response).strip()
                if text and not text.startswith('{"error"') and len(text) > 300:
                    return '# Expert Director Report\n\n' + text + '\n', 'live-ai'
        raise ValueError('Expert returned no usable edit report')
    except (requests.RequestException, ValueError, OSError) as exc:
        sections = [f'# Expert Director Report — telemetry-only fallback\n\n'
                    f'Live AI consultation failed ({type(exc).__name__}); the decisions below '
                    'are deterministic and require a human visual review of each contact sheet.\n\n'
                    f'**Source title (not visual evidence):** {title}\n']
        for plan in plans:
            t = plan['telemetry']
            start, peak, end = plan['start'], plan['peak'], plan['start'] + plan['duration']
            rows = [('Lineup', 0, 'Open on a clear vehicle frame; establish the route.', 'No cue; leave source audio readable.'),
                    ('Acceleration', t['launch'], 'Follow the measured motion surge with a smooth crop.', 'turbo_whoosh.wav + yippee_cute.wav for launch energy.'),
                    ('Obstacle approaching', t['bump'], 'Hold enough context to see the hazard; avoid blind zoom.', 'pop_bubble.wav for the local wobble.'),
                    ('Pre-impact', t['panic'], 'Tighten framing without hiding the impact path.', 'huh_cat.wav + crowd_gasp.wav at the measured panic beat.'),
                    ('Collision', t['impact'], 'Cut on contact; use one damped 0.3s camera pulse.', 'vine_boom.wav + bonk_meme.wav on the primary hit.'),
                    ('Wreckage', t['secondary'], 'Hold the second hit, then release into aftermath.', 'roblox_oof.wav at the secondary peak; cute_giggle.wav at aftermath.')]
            sections.append(f"\n## Candidate {plan['candidate']} — EDL\n\n"
                            f"Source window: **{start:.2f}–{end:.2f}s**, impact **{peak:.2f}s**; "
                            f"rank score **{plan['rank_score']:.2f}**, run-up motion **{t['runup_speed_score']:.2f}**, "
                            f"collision chaos **{t['collision_chaos_score']:.2f}**, airborne flag **{t['airborne_detected']}**, "
                            f"secondary hit **+{t['secondary_hit_offset']:.2f}s**.\n\n"
                            f"Review contact sheet: `{plan['contact_sheet']}`.\n\n"
                            '| Shot | Source time | Edit decision | Sound rationale |\n|---|---:|---|---|\n')
            sections.extend(f'| {name} | {start+at:.2f}s | {edit} | {sound} |\n'
                            for name, at, edit, sound in rows)
            sections.append(f"\nAftermath cue at **{start+t['aftermath']:.2f}s**. "
                            'Check vehicle identity, hazard type, and SFX rights against the footage; '
                            'motion/audio scores alone cannot establish a train, saw, or jump.\n')
        sections.append('\n## Packaging and safety\n\nUse the sharpest verified vehicle–hazard frame for covers; '
                        'do not invent unseen hazards. Keep US SEO scene-specific. '
                        'Final render targets CFR 60fps and -14 LUFS; verify the actual output before publishing.\n')
        return ''.join(sections), 'local-fallback'


def _director(source: Path, clips: list[Candidate], title: str, plans: list[dict]) -> dict:
    if AI_API_KEY:
        try:
            data, choice = direct(source, clips, title=title, model=AI_MODEL,
                                  storyboard_jpeg=Path(plans[0]['contact_sheet']).read_bytes(),
                                  storyboard_telemetry=[plan['telemetry'] for plan in plans])
            data['director_mode'] = 'live-ai'
            return data
        except (requests.RequestException, ValueError, OSError) as exc:
            print(f'Director unavailable: {type(exc).__name__}; local copy used.', flush=True)
    data = local_english_metadata(compilation=len(clips)>1)
    data['director_mode'] = 'local-fallback'
    return data


def _video_details(path: Path) -> dict:
    response = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                               '-show_entries', 'stream=avg_frame_rate,nb_read_frames,width,height',
                               '-show_entries', 'format=duration', '-of', 'json', str(path)],
                              capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL, close_fds=True)
    data = json.loads(response.stdout)
    video = data['streams'][0]
    loudness = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path),
                               '-vn', '-filter:a', 'ebur128=peak=true', '-f', 'null', '-'],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, close_fds=True)
    matches = re.findall(r'\bI:\s*(-?\d+(?:\.\d+)?) LUFS', loudness.stderr)
    return {'duration': float(data['format']['duration']), 'frames': int(video['nb_read_frames']),
            'fps': video['avg_frame_rate'], 'width': video['width'], 'height': video['height'],
            'measured_integrated_lufs': float(matches[-1]) if matches else None}


def analyze_video_storyboard(url: str) -> dict:
    source = acquire(url)
    title = _title(url, source)
    folder = project_folder(title, source.stem)
    clips = _candidates(source, 3)
    plans = _storyboards(source, clips, folder)
    report, mode = expert_report(title, plans, Path(plans[0]['contact_sheet']))
    (folder / 'expert_director_report.md').write_text(report, encoding='utf-8')
    return {'title': title, 'folder': str(folder.resolve()), 'storyboards': plans,
            'report': report, 'expert_mode': mode}


def process_one(url: str, mode: str = 'both', max_clips: int = 1,
                progress: Callable[[int, str], None] | None = None) -> dict:
    if mode not in ('both', 'short', 'classic59') or not 1 <= max_clips <= 9:
        raise ValueError('mode must be both/short/classic59 and max_clips must be 1..9')
    update = progress or (lambda percent, message: None)
    update(10, f'Downloading/caching {url}...')
    source = acquire(url)
    update(35, f'Analyzing crash peaks in {source.stem}...')
    title = _title(url, source)
    folder = project_folder(title, source.stem)
    candidates = _candidates(source, max_clips)
    plans = _storyboards(source, candidates[:max(3, max_clips)], folder)
    update(60, f'AI Director {AI_MODEL} for {source.stem}...')
    short_clip = candidates[0]
    comp_clips = sorted(candidates[:3], key=lambda c: c.start)
    short_data = _director(source, [short_clip], title, plans[:1])
    comp_data = _director(source, comp_clips, title, plans[:3]) if mode != 'short' else None
    # Consultation continues while FFmpeg encodes, bounded to two heavyweight jobs.
    memory = {}
    update(75, f'Preparing thumbnails, report and 60fps render for {source.stem}...')
    with ThreadPoolExecutor(max_workers=3) as stages:
        report_future = stages.submit(expert_report, title, plans, Path(plans[0]['contact_sheet']))
        def covers_and_sfx():
            ensure_sfx_pack()
            cover = short_data if mode != 'classic59' else comp_data
            vertical, horizontal = generate_thumbnails(source, short_clip, folder / 'cover',
                                                        cover['thumbnail_text'])
            vertical.replace(folder / 'thumb_vertical_1080x1920.jpg')
            horizontal.replace(folder / 'thumb_horizontal_1280x720.jpg')
            write_seo_us(folder / 'seo_us_copy_paste.txt', cover)
        cover_future = stages.submit(covers_and_sfx)
        if mode in ('short', 'both'):
            def short_render():
                with heavy_job() as stats:
                    result = render(source, short_clip, folder / 'short_01_viral_18s.mp4',
                                    sfx_density='hyper', director_memes=short_data['selected_memes'],
                                    hook_text=short_data['hook_text'], impact_text=short_data['impact_text'],
                                    event_times=plans[0]['telemetry'])
                memory['short_peak_rss_mb'] = stats['peak_process_tree_rss_mb']
                return result
            video_future = stages.submit(short_render)
        else:
            video_future = None
        report, report_mode = report_future.result()
        (folder / 'expert_director_report.md').write_text(report, encoding='utf-8')
        update(85, f'Expert report ready; finishing 60fps Short for {source.stem}...')
        if video_future:
            video_future.result()
        cover_future.result()
    if mode in ('classic59', 'both'):
        update(90, f'Rendering Classic59 compilation for {source.stem}...')
        segment_directors = []
        for segment in comp_clips:
            directed = short_data if abs(segment.peak-short_clip.peak) < .05 else comp_data
            segment_directors.append({'selected_memes': directed['selected_memes'],
                                      'hook_text': directed['hook_text'],
                                      'impact_text': directed['impact_text']})
        with heavy_job() as stats:
            compile_crashes(source, comp_clips, folder / 'classic59_compilation_57s.mp4',
                            shorts_mode='classic59', max_clips=3, sfx_density='hyper',
                            director_segments=segment_directors)
        memory['compilation_peak_rss_mb'] = stats['peak_process_tree_rss_mb']
    update(95, f'Verifying FPS, loudness and assets for {source.stem}...')
    cover_data = short_data if mode != 'classic59' else comp_data
    videos = {p.name: _video_details(p) for p in sorted(folder.glob('*.mp4'))}
    info = {'title': title, 'video_id': source.stem, 'source_url': url,
            'source_path': str(source.resolve()), 'source_fps': _source_fps(source),
            'timestamps': plans, 'videos': videos, 'loudness_target_lufs': -14,
            'ai_model': AI_MODEL if cover_data['director_mode'] == 'live-ai' else None,
            'director_mode': cover_data['director_mode'], 'expert_mode': report_mode,
            'memory_peak_process_tree_mb': memory,
            'hook_text': cover_data['hook_text'], 'thumbnail_text': cover_data['thumbnail_text']}
    (folder / 'project_info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return {'title': title, 'folder': str(folder.resolve()), 'assets':
            [str(p.resolve()) for p in sorted(folder.iterdir()) if p.is_file()], 'project_info': info}


def _source_fps(source: Path) -> str:
    response = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                               '-show_entries', 'stream=avg_frame_rate', '-of', 'csv=p=0', str(source)],
                              capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL, close_fds=True)
    return response.stdout.strip()


def process_youtube_links(urls: list[str] | str, mode: str = 'short', max_clips: int = 1,
                          progress: Callable[[int, str], None] | None = None) -> list[dict]:
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list) or not urls or not all(isinstance(url, str) for url in urls):
        raise ValueError('urls must be a URL string or a nonempty list of URLs')
    with worker_pool() as pool:
        futures = [pool.submit(process_one, url, mode, max_clips, progress) for url in urls]
        return [future.result() for future in futures]
