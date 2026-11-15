"""Loopback-only IronAxis Studio: project browser, real status, safe derivative renders."""
from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from config import OUTPUTS
from core.docu_postprocess import TEXT_BLUR_MODES
from core.studio_transitions import STYLES
from long_docu_pipeline import render_studio_derivative
from studio_ingest import (VIDEO_EXTENSIONS, download_source, import_local_video,
                           validate_source_url)

ROOT = Path(__file__).resolve().parent
PORT = 8765
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
MEDIA = {".mp4", ".wav", ".mp3", ".ass", ".srt", ".jpg", ".png"}
SLIDERS = {"voice": 94, "bgm": 36, "sfx": 54, "blur": 88, "sharp": 55, "contrast": 58}


def project_path(name: str) -> Path:
    if not re.fullmatch(r"IronAxisLab_[\w. -]{1,148}", name, re.UNICODE):
        raise ValueError("Invalid project ID")
    path = (OUTPUTS / name).resolve()
    if path.parent != OUTPUTS.resolve() or not path.is_dir():
        raise ValueError("Project not found")
    return path


def listed_projects() -> list[str]:
    return [p.name for p in sorted(OUTPUTS.iterdir()) if p.is_dir() and p.name.startswith('IronAxisLab_')
            and ((p / 'final_us_documentary_1080p60.mp4').is_file()
                 or any(q.suffix.lower() in VIDEO_EXTENSIONS for q in p.glob('source_01.*')))]


def probe(path: Path) -> dict:
    if not path.is_file():
        return {}
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,avg_frame_rate",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=12)
    return json.loads(result.stdout) if result.returncode == 0 else {}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (ValueError, OSError):
        return {}


def subtitle_cues(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    import pysrt
    try:
        return [{"start": c.start.ordinal / 1000, "end": c.end.ordinal / 1000, "text": c.text}
                for c in pysrt.open(str(path), encoding="utf-8")]
    except (OSError, ValueError):
        return []


def ass_cues(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    def clock(value: str) -> float:
        hour, minute, seconds = value.split(":")
        return int(hour) * 3600 + int(minute) * 60 + float(seconds)
    cues = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) == 10:
            try:
                cues.append({"start": clock(fields[1]), "end": clock(fields[2]), "text": fields[9]})
            except (ValueError, IndexError):
                continue
    return cues


def agent_assessments(folder: Path, videos: list[dict], assets: dict, cues: list[dict],
                      timeline: dict, settings: dict, backfill: dict, qa: dict) -> dict:
    """Evidence-based checks; 'ready' is not a performance or mix certification."""
    spans = timeline.get("segments", [])
    master = next((v for v in videos if v["relative"] == "final_us_documentary_1080p60.mp4"), None)
    stream = next((s for s in (master or {}).get("probe", {}).get("streams", []) if s.get("codec_type") == "video"), {})
    shorts = [v for v in videos if v["relative"].startswith("shorts/")]
    latest_qa = next((qa[v["relative"]] for v in reversed(videos) if v["relative"] in qa), None)
    qa_summary = (f"{latest_qa['lufs']:.2f} LUFS · {latest_qa['true_peak_dbtp']:.2f} dBTP"
                  if latest_qa else "bấm để đo LUFS/True Peak của MP4 đã chọn")
    levels = {
        "Plan": (bool(spans), f"{len(spans)} chương trong timeline"),
        "Voice": (assets["voice"], "file giọng có sẵn" if assets["voice"] else "thiếu giọng AI"),
        "Transcribe": (bool(cues), f"{len(cues)} cue có mốc thời gian; chưa xác nhận khớp nghe"),
        "Director": (bool(spans), f"{len(spans)} biên chương"),
        "Caption": (assets["ass"], "Master ASS 44pt" if assets["ass"] else "chưa có ASS"),
        "Demucs": (assets["sfx"], "stem tồn tại; cần nghe kiểm tra" if assets["sfx"] else "không có stem Demucs; source gốc đã mute"),
        "BlurMask": (bool(settings.get("text_blur_mode")), settings.get("text_blur_mode", "chưa bật/không rõ")),
        "Motion": (bool(spans), f"{max(0,len(spans)-1)} mốc; chọn hiệu ứng khi render"),
        "Music": (assets["bgm"], "BGM tái tạo, không phải bản mix cũ" if backfill.get("created", {}).get("bgm") else "stem nhạc hiện hữu" if assets["bgm"] else "thiếu BGM"),
        "Color": (assets["visual"], "visual stem; xem metadata để xác nhận filter"),
        "HUD": (bool(spans), "mốc HUD trong timeline" if spans else "chưa có dữ liệu HUD"),
        "QA": (bool(latest_qa and latest_qa.get("passed")), qa_summary),
        "Shorts": (len(shorts)==3, f"{len(shorts)}/3 Shorts"),
        "Render": (bool(master), f"{stream.get('width','?')}×{stream.get('height','?')} · {stream.get('avg_frame_rate','?')} fps"),
    }
    return {key: {"state": "available" if ok else "needs_review", "detail": detail}
            for key, (ok, detail) in levels.items()}


def preview_poster(folder: Path, video: Path) -> str | None:
    """Create a still for paused/headless preview; playback remains the source MP4."""
    if not video.is_file():
        return None
    target = folder / "studio_posters" / (video.stem + ".jpg")
    if not target.is_file():
        target.parent.mkdir(exist_ok=True)
        result = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", "0.6",
                                 "-i", str(video), "-frames:v", "1", "-q:v", "3", str(target)],
                                capture_output=True, timeout=90)
        if result.returncode:
            return None
    return target.relative_to(folder).as_posix()


def inventory(folder: Path) -> dict:
    outputs = [folder / "final_us_documentary_1080p60.mp4", *sorted((folder / "shorts").glob("short_*.mp4"))]
    if not outputs[0].is_file():
        outputs = [*sorted(p for p in folder.glob('source_01.*') if p.suffix.lower() in VIDEO_EXTENSIONS), *outputs]
    videos = [{"name": p.name, "relative": p.relative_to(folder).as_posix(), "probe": probe(p)}
              for p in outputs if p.is_file()]
    for video in videos:
        if video["relative"].startswith("shorts/") or video["relative"] == "final_us_documentary_1080p60.mp4" or video['relative'].startswith('source_01.'):
            video["poster"] = preview_poster(folder, folder / video["relative"])
    assets = {key: p.is_file() for key, p in {
        "voice": folder / "full_voiceover.wav", "sfx": folder / "ambient_bed.wav",
        "visual": folder / "visual_master_silent.mp4", "bgm": folder / "bgm_master.wav",
        "ass": folder / "english_narration.ass", "mix": folder / "master_audio_mix.wav"}.items()}
    qa = read_json(folder / "studio_qa.json")
    latest = sorted((p for p in (folder / "studio_renders").glob("studio_*.mp4")
                     if p.is_file() and p.stat().st_size > 0 and p.relative_to(folder).as_posix() in qa),
                    key=lambda p: p.stat().st_mtime) \
        if (folder / "studio_renders").is_dir() else []
    if latest:
        p = latest[-1]
        videos.append({"name": p.name, "relative": p.relative_to(folder).as_posix(), "probe": probe(p)})
    cues = subtitle_cues(folder / "english_narration.srt")
    short_cues = {v["relative"]: ass_cues(folder / Path(v["relative"]).with_suffix(".ass"))
                  for v in videos if v["relative"].startswith("shorts/")}
    timeline = read_json(folder / "english_narration_timeline.json")
    settings = read_json(folder / "production_settings.json")
    backfill = read_json(folder / "studio_backfill.json")
    return {"id": folder.name, "videos": videos, "assets": assets,
            "render_ready": all(assets[k] for k in ("voice", "bgm", "visual", "ass")),
            "settings": settings, "backfill": backfill, "qa": qa, "cues": cues, "short_cues": short_cues,
            "info": read_json(folder / "project_info.json"),
            "script": read_json(folder / "script.json"),
            "timeline": timeline,
            "agents": agent_assessments(folder, videos, assets, cues, timeline, settings, backfill, qa)}


def status() -> dict:
    log = OUTPUTS / "live_status.log"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-35:] if log.is_file() else []
    entries = [read_json(p) for p in sorted((OUTPUTS / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime)[-25:]] \
        if (OUTPUTS / "jobs").is_dir() else []
    with LOCK:
        studio = list(JOBS.values())
    combined = {job.get("job_id", str(index)): job for index, job in enumerate(entries + studio)}
    return {"lines": lines, "jobs": list(combined.values())}


def set_job(job_id: str, **changes) -> None:
    with LOCK:
        JOBS[job_id].update(changes, updated=datetime.now(timezone.utc).isoformat())
        snapshot = dict(JOBS[job_id])
    path = OUTPUTS / 'jobs' / f'studio_{job_id}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')


def start_job(kind: str, project: str = '') -> str:
    job_id = uuid4().hex[:12]
    with LOCK:
        JOBS[job_id] = {'job_id': job_id, 'kind': kind, 'project': project,
                        'status': 'queued', 'message': kind + ' queued'}
    set_job(job_id)
    return job_id


def download_worker(job_id: str, options: dict) -> None:
    try:
        set_job(job_id, status='running', message='Downloading source')
        def progress(percent, speed):
            set_job(job_id, progress_percent=percent, speed_mbps=speed,
                    message=f'Downloading {percent:.1f}% · {speed:.2f} MiB/s')
        result = download_source(OUTPUTS, progress=progress, **options)
        set_job(job_id, status='completed', message='Source ready; choose four pre-flight options', **result)
    except Exception as exc:
        set_job(job_id, status='failed', message=f'{type(exc).__name__}: {str(exc)[-500:]}')


def validate_pipeline(data: dict) -> tuple[Path, dict]:
    from core.audio_separator import SOURCE_AUDIO_MODES
    from core.bgm_composer import TRACKS
    folder = project_path(data.get('project', ''))
    if any((folder / name).is_file() for name in ('final_us_documentary_1080p60.mp4', 'script.json')):
        raise ValueError('Pipeline only accepts newly imported source projects, not completed projects')
    source = next((p for p in folder.glob('source_01.*') if p.suffix.lower() in VIDEO_EXTENSIONS), None)
    if source is None:
        raise ValueError('Import a source video first')
    required = ('source_audio_mode', 'text_blur_mode', 'bgm_style', 'niche_mode')
    if any(not data.get(key) for key in required):
        raise ValueError('Choose all four pre-flight options before running')
    if data['source_audio_mode'] not in SOURCE_AUDIO_MODES or data['text_blur_mode'] not in TEXT_BLUR_MODES:
        raise ValueError('Unsupported audio or blur mode')
    if data['source_audio_mode'] == 'keep_raw_low' and data.get('source_audio_verified') is not True:
        raise ValueError('Raw source audio requires explicit machine-only verification')
    if data['bgm_style'] not in ('dynamic_multi', 'none', 'craftsman', *TRACKS):
        raise ValueError('Unsupported BGM style')
    if data['niche_mode'] not in ('engineering_machining', 'woodworking_bonsai', 'survival_commentary'):
        raise ValueError('Unsupported niche')
    if data.get('transition_style', 'none') not in STYLES:
        raise ValueError('Unsupported transition')
    if any(data.get(key) is False for key in ('make_intro_hook', 'make_shorts')):
        raise ValueError('Intro Hook and three Shorts are mandatory in this pipeline')
    if data.get('brand_title') not in (None, 'IRONAXIS LAB'):
        raise ValueError('Custom export brand title is not yet supported; preview overlay only')
    sliders = data.get('sliders', SLIDERS)
    if set(sliders) != set(SLIDERS) or any(type(v) not in (int, float) or not 0 <= v <= 100 for v in sliders.values()):
        raise ValueError('Invalid sliders')
    return folder, {'niche_mode': data['niche_mode'], 'source_audio_mode': data['source_audio_mode'],
                    'text_blur_mode': data['text_blur_mode'], 'bgm_style': data['bgm_style'],
                    'transition_style': data.get('transition_style', 'none'),
                    'clean_outro_sec': 12 if data.get('clean_outro') else 0,
                    'render_settings': {k: sliders[k] for k in ('blur', 'sharp', 'contrast')},
                    'existing_project_dir': folder, 'local_source_files': [source]}


def pipeline_worker(job_id: str, options: dict) -> None:
    from long_docu_pipeline import produce_documentary
    try:
        set_job(job_id, status='running', message='Producing documentary')
        result = produce_documentary([], progress=lambda message: set_job(job_id, message=message), **options)
        set_job(job_id, status='completed', message='Pipeline complete; review output before release', result=result)
    except Exception as exc:
        set_job(job_id, status='failed', message=f'{type(exc).__name__}: {str(exc)[-500:]}')


def validate_render(payload: dict) -> tuple[Path, dict, str]:
    folder = project_path(payload.get("project", ""))
    settings = payload.get("sliders")
    if not isinstance(settings, dict) or set(settings) != set(SLIDERS) or any(
            type(v) not in (int, float) or not 0 <= v <= 100 for v in settings.values()):
        raise ValueError("Six numeric sliders in 0..100 are required")
    mode = payload.get("text_blur_mode", "none")
    if mode not in TEXT_BLUR_MODES or mode == "auto_detect_blur":
        raise ValueError("Select an explicit blur mode for a rerender")
    item = inventory(folder)
    if not item["render_ready"]:
        raise ValueError("Missing individual voice/BGM/visual/ASS stem. Legacy project cannot safely remix")
    if settings["sfx"] != SLIDERS["sfx"] and not item["assets"]["sfx"]:
        raise ValueError("This project has no Demucs ambience stem for the SFX slider")
    if payload.get("transition_style", "none") not in STYLES:
        raise ValueError("Invalid transition style")
    return folder, settings, mode


def measure_export_audio(path: Path) -> tuple[float, float]:
    """Measure the *encoded MP4* (not the upstream loudnorm prediction)."""
    result = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vn",
                             "-af", "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json",
                             "-f", "null", "-"], capture_output=True, text=True, timeout=7200)
    if result.returncode:
        raise RuntimeError(result.stderr[-500:])
    integrated = re.search(r'"input_i"\s*:\s*"([^"\n]+)"', result.stderr)
    true_peak = re.search(r'"input_tp"\s*:\s*"([^"\n]+)"', result.stderr)
    if not integrated or not true_peak:
        raise RuntimeError("FFmpeg did not return encoded loudness and true peak")
    return float(integrated.group(1)), float(true_peak.group(1))


def master_encoded_audio(output: Path) -> tuple[float, float, bool]:
    """Correct AAC peak overshoot without re-encoding video; only promote a QA-passing file."""
    measured = measure_export_audio(output)
    if -15 <= measured[0] <= -13 and measured[1] <= -1:
        return *measured, True
    audio = output.with_name(output.stem + "_audiofix.m4a")
    remux = output.with_name(output.stem + "_remux.mp4")
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(output), "-vn",
                        "-af", "acompressor=threshold=0.18:ratio=2:attack=10:release=100:"
                        "makeup=1.5,alimiter=limit=0.50:level=0",
                        "-c:a", "aac", "-b:a", "256k", str(audio)],
                       check=True, capture_output=True, timeout=1800)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(output),
                        "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
                        "-movflags", "+faststart", str(remux)],
                       check=True, capture_output=True, timeout=1800)
        corrected = measure_export_audio(remux)
        if -15 <= corrected[0] <= -13 and corrected[1] <= -1:
            remux.replace(output)
            return *corrected, True
        return *measured, False
    finally:
        audio.unlink(missing_ok=True)
        remux.unlink(missing_ok=True)


def record_qa(folder: Path, relative: str, measured_i: float, measured_tp: float) -> bool:
    passed = -15 <= measured_i <= -13 and measured_tp <= -1
    path = folder / "studio_qa.json"
    records = read_json(path)
    records[relative] = {"lufs": measured_i, "true_peak_dbtp": measured_tp, "passed": passed,
                         "updated": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return passed


def render_worker(job_id: str, folder: Path, sliders: dict, mode: str,
                  transition_style: str = "none") -> None:
    def update(state: str, message: str, **extras):
        with LOCK:
            JOBS[job_id].update(status=state, message=message,
                                updated=datetime.now(timezone.utc).isoformat(), **extras)
            snapshot = dict(JOBS[job_id])
        job_file = OUTPUTS / "jobs" / f"studio_{job_id}.json"
        job_file.parent.mkdir(parents=True, exist_ok=True)
        job_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        destination = folder / "studio_renders"
        destination.mkdir(exist_ok=True)
        output = destination / f"studio_{job_id}.mp4"
        update("running", "Encoding studio derivative (source outputs preserved)")
        render_studio_derivative(folder, output, sliders, mode, transition_style)
        measured_i, measured_tp, passed = master_encoded_audio(output)
        record_qa(folder, output.relative_to(folder).as_posix(), measured_i, measured_tp)
        update("completed", "Derivative QA passed; listen/review before release." if passed else
               "Derivative rendered; audio QA outside -14 ±1 LUFS / -1 dBTP, needs review.",
               output=output.relative_to(folder).as_posix(), loudness_lufs=measured_i,
               true_peak_dbtp=measured_tp, qa_passed=passed)
    except Exception as exc:
        # Only discard the incomplete derivative belonging to this job.
        incomplete = folder / "studio_renders" / f"studio_{job_id}.mp4"
        if incomplete.is_file():
            incomplete.unlink(missing_ok=True)
        update("failed", f"{type(exc).__name__}: {str(exc)[-500:]}")


def qa_worker(job_id: str, folder: Path, relative: str) -> None:
    try:
        path = (folder / relative).resolve()
        if path not in [(folder / v["relative"]).resolve() for v in inventory(folder)["videos"]]:
            raise ValueError("Unlisted video")
        with LOCK:
            JOBS[job_id].update(status="running", message="Measuring selected MP4 audio")
        measured_i, measured_tp = measure_export_audio(path)
        passed = record_qa(folder, relative, measured_i, measured_tp)
        with LOCK:
            JOBS[job_id].update(status="completed", message=f"QA {relative}: {measured_i:.2f} LUFS; "
                                 f"True Peak {measured_tp:.2f} dBTP",
                                 loudness_lufs=measured_i, true_peak_dbtp=measured_tp,
                                 qa_passed=passed,
                                 updated=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(status="failed", message=f"QA: {type(exc).__name__}: {str(exc)[-300:]}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, data: bytes, mime: str, status_code: int = 200, extra: dict | None = None):
        self.send_response(status_code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; media-src 'self'; connect-src 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send(json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8", code)

    def do_GET(self):
        try:
            uri = urlsplit(self.path)
            parts = [unquote(p) for p in uri.path.strip("/").split("/")]
            if uri.path == "/":
                self.send((ROOT / "studio_ui.html").read_bytes(), "text/html; charset=utf-8")
            elif uri.path == '/studio_enhancements.js':
                self.send((ROOT / 'studio_enhancements.js').read_bytes(), 'text/javascript; charset=utf-8')
            elif uri.path == '/studio_theme.css':
                self.send((ROOT / 'studio_theme.css').read_bytes(), 'text/css; charset=utf-8')
            elif uri.path == "/api/projects":
                self.send_json({'projects': listed_projects()})
            elif uri.path == "/api/status":
                self.send_json(status())
            elif len(parts) == 3 and parts[:2] == ["api", "project"]:
                self.send_json(inventory(project_path(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "analysis":
                self.send_json(inventory(project_path(parts[2])))
            elif len(parts) == 3 and parts[0] == "media":
                folder = project_path(parts[1])
                rel = Path(parts[2])
                self.media(folder, rel)
            elif len(parts) == 4 and parts[0] == "media" and parts[2] in ("shorts", "studio_renders", "studio_posters"):
                self.media(project_path(parts[1]), Path(parts[2]) / parts[3])
            else:
                self.send_json({"error": "Not found"}, 404)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)

    def media(self, folder: Path, rel: Path):
        path = (folder / rel).resolve()
        if not path.is_file() or not path.is_relative_to(folder) or path.suffix.lower() not in MEDIA:
            raise ValueError("Media not found")
        size = path.stat().st_size
        req = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", req) if req else None
        if req and not match:
            self.send_json({"error": "Invalid range"}, 416)
            return
        start = int(match.group(1)) if match else 0
        end = min(size-1, int(match.group(2))) if match and match.group(2) else size-1
        if start >= size or end < start:
            self.send_json({"error": "Range unavailable"}, 416)
            return
        self.send_response(206 if match else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end-start+1))
        if match:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as file:
                file.seek(start)
                remaining = end-start+1
                while remaining:
                    block = file.read(min(1024*1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        if self.path not in ("/api/render", "/api/render_derivative", "/api/qa",
                             "/api/pick_local_video", "/api/upload_local_video",
                             "/api/download_source_url", "/api/run_full_pipeline"):
            return self.send_json({"error": "Not found"}, 404)
        if self.headers.get("Origin", "http://127.0.0.1:8765") != "http://127.0.0.1:8765":
            return self.send_json({"error": "Foreign origin"}, 403)
        if self.path == '/api/upload_local_video':
            if self.headers.get('Content-Type') != 'application/octet-stream':
                return self.send_json({'error': 'Binary upload required'}, 415)
            try:
                length = int(self.headers.get('Content-Length', 0))
                filename = Path(self.headers.get('X-Filename', '')).name
                if not 0 < length <= 2 * 1024**3 or Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
                    raise ValueError('Upload must be a supported video, at most 2 GiB')
                with tempfile.TemporaryDirectory(prefix='ironaxis_ingest_') as directory:
                    temp = Path(directory) / ('source' + Path(filename).suffix.lower())
                    with temp.open('xb') as out:
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(4 * 1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError('Incomplete upload')
                            out.write(chunk)
                            remaining -= len(chunk)
                    result = import_local_video(OUTPUTS, temp, self.headers.get('X-Project-Name'), filename)
                return self.send_json(result, 201)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                return self.send_json({'error': str(exc)}, 400)
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return self.send_json({"error": "JSON required"}, 415)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 8192:
                raise ValueError("Invalid JSON length")
            data = json.loads(self.rfile.read(length))
            if self.path == '/api/pick_local_video':
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                try:
                    root.withdraw()
                    root.attributes('-topmost', True)
                    chosen = filedialog.askopenfilename(parent=root, filetypes=[
                        ('Video files', '*.mp4 *.mov *.mkv *.webm *.avi')])
                finally:
                    root.destroy()
                if not chosen:
                    return self.send_json({'cancelled': True})
                return self.send_json(import_local_video(OUTPUTS, Path(chosen), data.get('project_name')), 201)
            if self.path == '/api/download_source_url':
                url = validate_source_url(data.get('url'))
                options = {'url': url, 'project_name': data.get('project_name'),
                           'use_cookies': data.get('use_cookies', False),
                           'browser': data.get('browser', 'edge'), 'quality': data.get('quality', '1080')}
                if type(options['use_cookies']) is not bool or options['browser'] not in ('edge','chrome') or options['quality'] not in ('1080','720'):
                    raise ValueError('Invalid download options')
                job_id = start_job('download')
                threading.Thread(target=download_worker, args=(job_id, options), daemon=True).start()
                return self.send_json(JOBS[job_id], 202)
            if self.path == '/api/run_full_pipeline':
                folder, options = validate_pipeline(data)
                job_id = start_job('pipeline', folder.name)
                threading.Thread(target=pipeline_worker, args=(job_id, options), daemon=True).start()
                return self.send_json(JOBS[job_id], 202)
            if self.path == "/api/qa":
                folder = project_path(data.get("project", ""))
                relative = data.get("relative", "")
                if relative not in [v["relative"] for v in inventory(folder)["videos"]]:
                    raise ValueError("Selected MP4 not found")
                job_id = uuid4().hex[:12]
                with LOCK:
                    JOBS[job_id] = {"job_id": job_id, "project": folder.name, "status": "queued",
                                    "message": f"QA measuring {relative}", "updated": datetime.now(timezone.utc).isoformat()}
                threading.Thread(target=qa_worker, args=(job_id, folder, relative), daemon=True).start()
                return self.send_json(JOBS[job_id], 202)
            folder, sliders, mode = validate_render(data)
            job_id = uuid4().hex[:12]
            with LOCK:
                JOBS[job_id] = {"job_id": job_id, "project": folder.name, "status": "queued",
                                "message": "Queued studio derivative", "updated": datetime.now(timezone.utc).isoformat()}
            job_file = OUTPUTS / "jobs" / f"studio_{job_id}.json"
            job_file.parent.mkdir(parents=True, exist_ok=True)
            job_file.write_text(json.dumps(JOBS[job_id], ensure_ascii=False, indent=2), encoding="utf-8")
            threading.Thread(target=render_worker,
                             args=(job_id, folder, sliders, mode, data.get("transition_style", "none")),
                             daemon=True).start()
            self.send_json(JOBS[job_id], 202)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
            self.send_json({"error": str(exc)}, 400)


if __name__ == "__main__":
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    print(f"IronAxis Studio: http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
