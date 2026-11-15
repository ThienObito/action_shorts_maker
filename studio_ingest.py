"""Constrained source intake for loopback Studio; never overwrite existing media."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.avi'}
HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be',
         'facebook.com', 'www.facebook.com', 'm.facebook.com', 'fb.watch',
         'tiktok.com', 'www.tiktok.com', 'vm.tiktok.com', 'douyin.com', 'www.douyin.com'}


def validate_source_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Invalid source URL')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError('Only HTTPS links from supported video hosts are allowed')
    return url


def project_label(name: str | None) -> str:
    slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', (name or 'new_video').strip()).strip('_')[:48]
    return 'IronAxisLab_' + (slug or 'new_video') + '_' + uuid4().hex[:8]


def video_metadata(path: Path) -> dict:
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                             'stream=codec_type,width,height,avg_frame_rate:format=duration',
                             '-of', 'json', str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError('Selected file is not a readable video')
    data = json.loads(result.stdout)
    stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not stream or float(data.get('format', {}).get('duration', 0)) <= 0:
        raise ValueError('Selected file has no valid video stream/duration')
    return {'width': stream.get('width'), 'height': stream.get('height'),
            'fps': stream.get('avg_frame_rate'), 'duration': float(data['format']['duration'])}


def import_local_video(outputs: Path, source: Path, project_name: str | None = None,
                       original_filename: str | None = None) -> dict:
    source = Path(source).resolve()
    if source.suffix.lower() not in VIDEO_EXTENSIONS or not source.is_file():
        raise ValueError('Choose a supported video file')
    metadata = video_metadata(source)
    display_name = Path(original_filename).name if original_filename else source.name
    folder = outputs / project_label(project_name or Path(display_name).stem)
    folder.mkdir(parents=True, exist_ok=False)
    dest = folder / ('source_01' + source.suffix.lower())
    try:
        with source.open('rb') as inp, dest.open('xb') as out:
            shutil.copyfileobj(inp, out, 4 * 1024 * 1024)
        (folder / 'source_import.json').write_text(json.dumps({'kind': 'local', 'filename': display_name,
                                                                'metadata': metadata}, indent=2), encoding='utf-8')
    except Exception:
        dest.unlink(missing_ok=True)
        folder.rmdir()
        raise
    return {'project': folder.name, 'source': dest.name, 'metadata': metadata,
            'note': 'Source imported; pipeline has not run yet.'}


def download_source(outputs: Path, url: str, project_name: str | None = None,
                    use_cookies: bool = False, browser: str = 'edge', quality: str = '1080',
                    progress=None) -> dict:
    import yt_dlp
    url = validate_source_url(url)
    if browser not in ('edge', 'chrome') or quality not in ('1080', '720') or type(use_cookies) is not bool:
        raise ValueError('Invalid download options')
    folder = outputs / project_label(project_name)
    folder.mkdir(parents=True, exist_ok=False)
    options = {'format': ('bestvideo[height>=1080]+bestaudio/bestvideo[height>=720]+bestaudio/best' if quality == '1080'
                          else 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'),
               'merge_output_format': 'mp4', 'outtmpl': str(folder / 'source_01.%(ext)s'),
               'noplaylist': True, 'quiet': True, 'socket_timeout': 15, 'retries': 1,
               'fragment_retries': 1, 'progress_hooks': []}
    def hook(item):
        if progress and item.get('status') == 'downloading':
            total = item.get('total_bytes') or item.get('total_bytes_estimate') or 0
            progress(round(item.get('downloaded_bytes', 0) / total * 100, 1) if total else 0,
                     round((item.get('speed') or 0) / 1048576, 2))
    options['progress_hooks'].append(hook)
    attempts = (browser, 'chrome' if browser == 'edge' else 'edge') if use_cookies else (None,)
    errors = []
    for candidate in attempts:
        try:
            opts = {**options, **({'cookiesfrombrowser': (candidate,)} if candidate else {})}
            with yt_dlp.YoutubeDL(opts) as client:
                info = client.extract_info(url, download=True)
            source = next((p for p in folder.glob('source_01.*') if p.suffix.lower() in VIDEO_EXTENSIONS), None)
            if source is None:
                raise ValueError('Downloader produced no supported video')
            metadata = video_metadata(source)
            if not project_name and isinstance(info, dict):
                named = outputs / project_label(info.get('title'))
                folder.replace(named)
                folder = named
                source = folder / source.name
            (folder / 'source_import.json').write_text(json.dumps({'kind': 'url', 'url': url,
                                                                    'metadata': metadata}, indent=2), encoding='utf-8')
            return {'project': folder.name, 'source': source.name, 'metadata': metadata,
                    'note': 'Source downloaded; pipeline has not run yet.'}
        except Exception as exc:
            errors.append(f'{candidate or "without cookies"}: {type(exc).__name__}: {str(exc)[-160:]}')
    # Preserve failed/partial download for diagnosis, but never expose as a project.
    archive = outputs / '_archived_empty'
    archive.mkdir(exist_ok=True)
    folder.replace(archive / folder.name)
    raise ValueError('Download failed; archived incomplete project: ' + ' | '.join(errors))
