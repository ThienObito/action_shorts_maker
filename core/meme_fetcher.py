"""Fetch short recorded meme sounds, normalize them, and record their origin.

These third-party recordings may be copyrighted. Users are responsible for
checking rights before publishing a video containing them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
import soundfile as sf
import numpy as np
from bs4 import BeautifulSoup

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import ROOT

MEME_DIR = ROOT / "assets" / "sfx" / "real_memes"
MANIFEST = MEME_DIR / "sources.json"
BASE_URL = "https://www.myinstants.com"
SOUNDS = {
    "baby_laugh": ("baby laughing meme", "baby laughing"),
    "oh_no_voice": ("oh no meme", "oh no kreepa"),
    "bonk_meme": ("bonk doge", "metal pipe falling"),
    "vine_boom": ("vine boom",),
    "cartoon_slip": ("cartoon slip", "banana peel slip"),
    "boing_cartoon": ("cartoon boing", "mario jump"),
    "airhorn_mlg": ("mlg airhorn", "airhorn"),
    "thomas_theme_horn": ("thomas the tank engine horn", "train horn meme"),
    "police_siren_real": ("wee woo patrick", "police siren meme"),
    "cartoon_run_scurry": ("cartoon running", "scooby doo running"),
    "what_the_meme": ("what the dog doin", "huh cat meme"),
    "goofy_scream": ("goofy yell", "tom scream"),
    "bruh_meme": ("bruh",),
    "sad_violin_fail": ("spongebob fail", "sad trombone"),
    "anime_wow_yay": ("children yay", "anime wow"),
    "mario_jump": ("super mario jump", "mario jump sound"),
    "record_scratch": ("record scratch", "vinyl scratch"),
    "windows_error": ("windows xp error", "windows error sound"),
    "roblox_oof": ("roblox oof", "oof"),
    "among_us_sus": ("among us sus", "among us emergency"),
    "spongebob_fail": ("spongebob fail", "spongebob disappointed"),
    "taco_bell_bong": ("taco bell bong", "taco bell sound"),
    "metal_pipe": ("metal pipe falling", "metal pipe"),
    "cartoon_pop": ("pop sound effect", "pop"),
    "wow_voice": ("wow meme sound", "anime wow"),
    "dramatic_gasp": ("crowd gasp", "shocked sound effect gasp"),
    "mission_failed": ("mission failed", "mission failed meme"),
    "victory_cheer": ("kids cheering", "yay cheering"),
    "yippee_cute": ("tbh creature yippee", "yippee meme"),
    "cute_giggle": ("cute baby giggle", "anime giggle"),
    "pop_bubble": ("bubble pop sound", "pop cat"),
    "squeaky_toy": ("squeaky toy sound", "dog toy squeak"),
    "duck_quack": ("duck quack meme", "duck quack"),
    "huh_cat": ("huh cat meme", "huh cat"),
    "pikachu_cute": ("pikachu cute sound", "pikachu"),
    "tire_screech": ("tire screech", "car skid"),
    "turbo_whoosh": ("fast whoosh", "jet flyby"),
    "spring_jump": ("sonic spring", "trampoline bounce"),
    "glass_shatter": ("glass breaking meme", "glass shatter"),
    "crowd_gasp": ("crowd gasp", "anime gasp"),
}
VOICES = {
    "oh_no_voice": "Oh no! Oh no!",
    "what_the_meme": "Wait for it!",
    "goofy_scream": "Watch out!",
    "anime_wow_yay": "Wow! Yay!",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ActionShortsMaker/1.0)"}


def search_myinstants(query: str, session: requests.Session | None = None) -> list[tuple[str, str]]:
    """Return labeled direct MP3 URLs from result cards, never arbitrary hosts."""
    client = session or requests.Session()
    response = client.get(f"{BASE_URL}/en/search/?name={quote(query)}",
                          headers=HEADERS, timeout=12)
    response.raise_for_status()
    results = []
    for card in BeautifulSoup(response.text, "html.parser").select("div.instant"):
        button = card.select_one("button.small-button[onclick]")
        link = card.select_one("a.instant-link")
        if not button or not link:
            continue
        match = re.search(r"(/media/sounds/[^'\"\s]+?\.mp3)", button.get("onclick", ""))
        if match:
            url = urljoin(BASE_URL, match.group(1))
            if urlparse(url).hostname == "www.myinstants.com":
                results.append((link.get_text(" ", strip=True), url))
    return results


def _candidates(query: str, session: requests.Session) -> list[tuple[str, str]]:
    words = set(re.findall(r"[a-z0-9]+", query.lower())) - {"meme", "sound", "effect", "the"}
    results = search_myinstants(query, session)
    return sorted(results, key=lambda item: len(words & set(re.findall(r"[a-z0-9]+", item[0].lower()))),
                  reverse=True)[:8]


def _download_mp3(url: str, destination: Path, session: requests.Session) -> None:
    if urlparse(url).scheme != "https" or urlparse(url).hostname != "www.myinstants.com":
        raise ValueError("Unexpected audio host")
    with session.get(url, headers=HEADERS, timeout=15, stream=True) as response:
        response.raise_for_status()
        size = 0
        with destination.open("wb") as target:
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 8_000_000:
                    raise ValueError("Sound is too large")
                target.write(chunk)
    if size < 1000:
        raise ValueError("Empty or invalid audio download")


def _probe_duration(source: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
                             capture_output=True, text=True, check=True, timeout=15,
                             stdin=subprocess.DEVNULL, close_fds=True)
    return float(result.stdout.strip())


def normalize_audio(source: Path, destination: Path, *, start: float = 0) -> dict:
    """Trim silence, keep 0.5–2.2 seconds, and write normalized stereo PCM."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = ("silenceremove=start_periods=1:start_silence=0.05:start_threshold=-40dB:"
               "stop_periods=-1:stop_silence=0.05:stop_threshold=-40dB,"
               "atrim=duration=2.2,asetpts=PTS-STARTPTS,"
               "loudnorm=I=-14:TP=-1.0:LRA=11")
    with tempfile.TemporaryDirectory(prefix="actionshorts_meme_convert_") as folder:
        staged = Path(folder) / "normalized.wav"
        command = ["ffmpeg", "-y", "-v", "error"]
        if start:
            command += ["-ss", f"{start:.3f}"]
        command += ["-i", str(source), "-vn", "-af", filters,
                    "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(staged)]
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=30,
                       stdin=subprocess.DEVNULL, close_fds=True)
        data, samplerate = sf.read(staged, always_2d=True)
        if samplerate != 44100 or not 0.5 <= len(data) / samplerate <= 2.21:
            raise ValueError("Audio too short/long after silence removal")
        peak = float(abs(data).max())
        if peak < 0.005:
            raise ValueError("Sound is silent")
        gain_db = -1.0 - 20 * math.log10(peak)
        final = Path(folder) / "peak_normalized.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(staged),
                        "-af", f"volume={gain_db:.6f}dB", "-ar", "44100", "-ac", "2",
                        "-c:a", "pcm_s16le", str(final)], capture_output=True,
                        text=True, check=True, timeout=20,
                        stdin=subprocess.DEVNULL, close_fds=True)
        final_data, samplerate = sf.read(final, always_2d=True)
        final_peak = float(abs(final_data).max())
        final.replace(destination)
        return {"duration": round(len(final_data) / samplerate, 3),
                "peak_dbfs": round(20 * math.log10(final_peak), 2)}


def _youtube_audio(query: str, temp: Path) -> tuple[Path, str, float]:
    """Fallback to a short search result only; never fetch long videos."""
    import yt_dlp

    def short_only(info, *, incomplete=False):
        if info.get("duration") is None or info["duration"] > 15:
            return "No verified duration <= 15 seconds"
        return None

    options = {"format": "bestaudio/best", "outtmpl": str(temp / "youtube.%(ext)s"),
               "quiet": True, "noplaylist": True, "match_filter": short_only,
               "socket_timeout": 10, "retries": 1}
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(f"ytsearch1:{query} sound effect", download=True)
        entries = info.get("entries", []) if info else []
        entry = next((item for item in entries if item), None)
        if not entry:
            raise ValueError("No short YouTube result")
        files = list(temp.glob("youtube.*"))
        if not files:
            raise ValueError("YouTube audio missing")
        # Find the loudest 2.2-second window in short hits, without loading video.
        duration = _probe_duration(files[0])
        if duration > 15:
            raise ValueError("YouTube result exceeds the 15-second limit")
        start = 0.0
        if duration > 2.2:
            decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(files[0]),
                                      "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"],
                                      capture_output=True, check=True, timeout=30,
                                      stdin=subprocess.DEVNULL, close_fds=True)
            samples = np.frombuffer(decoded.stdout, dtype="<f4")
            window = 2 * 8000
            if len(samples) > window:
                energy = np.concatenate(([0.0], np.cumsum(samples.astype(np.float64) ** 2)))
                start = float(np.argmax(energy[window:] - energy[:-window])) / 8000
        return files[0], entry.get("webpage_url", ""), start


def _voice_audio(text: str, temp: Path) -> Path:
    import edge_tts
    path = temp / "voice.mp3"
    asyncio.run(edge_tts.Communicate(text, "en-US-AnaNeural", rate="+15%").save(str(path)))
    return path


def available_memes() -> dict[str, Path]:
    return {name: MEME_DIR / f"{name}.wav" for name in SOUNDS
            if (MEME_DIR / f"{name}.wav").is_file() and
            (MEME_DIR / f"{name}.wav").stat().st_size > 1000}


def fetch_meme_pack(*, force: bool = False, allow_youtube: bool = True,
                    allow_tts: bool = True, names: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Fetch real clips, then voice TTS where possible; retain a provenance manifest."""
    MEME_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    session = requests.Session()
    for name in names or tuple(SOUNDS):
        queries = SOUNDS[name]
        output = MEME_DIR / f"{name}.wav"
        if not force and output.is_file() and output.stat().st_size > 1000:
            continue
        errors = []
        completed = False
        with tempfile.TemporaryDirectory(prefix="actionshorts_meme_") as folder:
            temp = Path(folder)
            tried = set()
            for query in queries:
                try:
                    matches = _candidates(query, session)
                except (requests.RequestException, ValueError) as exc:
                    errors.append(f"search {query}: {exc}")
                    continue
                for title, url in matches:
                    if name == "thomas_theme_horn" and not any(
                            cue in title.lower() for cue in ("horn", "whistle", "toot")):
                        continue
                    if url in tried:
                        continue
                    tried.add(url)
                    try:
                        mp3 = temp / "candidate.mp3"
                        _download_mp3(url, mp3, session)
                        stats = normalize_audio(mp3, output)
                        manifest[name] = {"source": "myinstants", "title": title,
                                          "url": url, **stats}
                        completed = True
                        break
                    except (ValueError, OSError, subprocess.SubprocessError) as exc:
                        errors.append(f"{title}: {exc}")
                if completed:
                    break
            if not completed and allow_youtube:
                try:
                    audio, url, start = _youtube_audio(queries[0], temp)
                    stats = normalize_audio(audio, output, start=start)
                    manifest[name] = {"source": "youtube", "url": url, **stats}
                    completed = True
                except Exception as exc:
                    errors.append(f"youtube: {exc}")
            if not completed and allow_tts and name in VOICES:
                try:
                    audio = _voice_audio(VOICES[name], temp)
                    stats = normalize_audio(audio, output)
                    manifest[name] = {"source": "edge-tts", "text": VOICES[name], **stats}
                    completed = True
                except Exception as exc:
                    errors.append(f"edge-tts: {exc}")
        if not completed:
            manifest[name] = {"source": "unavailable", "errors": errors[-3:]}
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{name}: {manifest[name]['source']}" +
              (f" ({manifest[name].get('title', manifest[name].get('url', ''))})" if completed else ""),
              flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch and normalize 15 real meme sounds")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-youtube", action="store_true")
    parser.add_argument("--only", choices=SOUNDS)
    args = parser.parse_args()
    results = fetch_meme_pack(force=args.refresh, allow_youtube=not args.no_youtube,
                              names=(args.only,) if args.only else None)
    print(f"Available: {len(available_memes())}/{len(SOUNDS)}; manifest: {MANIFEST}")
