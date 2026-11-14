"""Download permitted YouTube content or copy a local video into the workspace."""
from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import INPUTS, ensure_dirs


def acquire(source: str | Path) -> Path:
    ensure_dirs()
    source = str(source).strip()
    local = Path(source).expanduser()
    if local.is_file():
        if local.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
            raise ValueError("Choose an MP4, MOV, MKV, or WebM video.")
        destination = INPUTS / local.name
        if local.resolve() != destination.resolve():
            shutil.copy2(local, destination)
        return destination
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"
    }:
        raise ValueError("Provide an existing local video or a YouTube URL.")
    video_id = parse_qs(parsed.query).get("v", [None])[0] if parsed.hostname != "youtu.be" else parsed.path.strip("/")
    if video_id and all(c.isascii() and (c.isalnum() or c in "_-" ) for c in video_id):
        cached = [p for p in INPUTS.glob(f"{video_id}.*") if p.suffix.lower() in {".mp4", ".webm", ".mkv"}]
        if cached:
            return cached[0]
    import yt_dlp

    options = {
        "format": "bestvideo[height<=1080][fps>=50][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        "outtmpl": str(INPUTS / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as client:
            info = client.extract_info(source, download=True)
    except yt_dlp.utils.DownloadError:
        # Equivalent to --extractor-args youtube:player_client=android,web.
        retry_options = {**options, "extractor_args": {"youtube": {"player_client": ["android", "web"]}}}
        with yt_dlp.YoutubeDL(retry_options) as client:
            info = client.extract_info(source, download=True)
    if info is None:
        raise RuntimeError("yt-dlp returned no video information.")
    video_id = info["id"]
    mp4 = INPUTS / f"{video_id}.mp4"
    if mp4.is_file():
        return mp4
    matches = [p for p in INPUTS.glob(f"{video_id}.*") if p.suffix.lower() in {".mp4", ".webm", ".mkv"}]
    if not matches:
        raise FileNotFoundError(f"Video download completed without a playable file for {video_id}.")
    return matches[0]
