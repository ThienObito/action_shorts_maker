"""Paths and rendering presets for Windows and Linux installations."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_env() -> None:
    """Load project-local secrets without overwriting explicitly set environment values."""
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))


_load_env()
AI_API_BASE = os.environ.get("AI_API_BASE", "https://php-tool-shop.proxy-api.shop/").rstrip("/")
CODEX_API_KEY = os.environ.get("CODEX_API_KEY") or os.environ.get("AI_API_KEY", "")
AI_API_KEY = CODEX_API_KEY  # backwards-compatible alias
AI_MODEL_CHOICES = ("gpt-5.6-sol", "gpt-6-sol", "gpt-6-luna")
AI_MODEL = os.environ.get("AI_MODEL", AI_MODEL_CHOICES[0])
AI_MARKET_LOCALE = os.environ.get("AI_MARKET_LOCALE", "en-US")
BIN = ROOT / "bin"
if BIN.is_dir():
    os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")
INPUTS = ROOT / ("downloads" if os.name == "nt" else "inputs")
OUTPUTS = ROOT / "outputs"
OUTPUTS = Path(os.environ.get("DOCU_OUTPUT_DIR", str(OUTPUTS))).expanduser().resolve()
MODELS = ROOT / "models"
MODEL_PATH = MODELS / "yolov8n.pt"
DEMO_URL = "https://www.youtube.com/watch?v=-brcT3E5Dcg"
DEMO_TITLE = "Big & Small McQueen Police VS Train | BeamNG.drive"
WIDTH, HEIGHT, FPS = 1080, 1920, 60
DEFAULT_FRAMING_MODE = "wide"
DEFAULT_FOV_SCALE = 1.75
MIN_FOV_SCALE, MAX_FOV_SCALE = 1.0, 2.5
FRAMING_MODES = {
    "wide": {"label": "Wide Balanced Action (1.75x FOV + Blurred Canvas)", "fov_scale": 1.75},
    "medium": {"label": "Medium Action View (1.45x FOV + Blurred Canvas)", "fov_scale": 1.45},
    "ultra": {"label": "Ultra-Wide Full Scene (2.25x FOV + Blurred Canvas)", "fov_scale": 2.25},
    "tight": {"label": "Tight Fullscreen Crop (1.0x Legacy)", "fov_scale": 1.0},
}
MIN_CLIP, MAX_CLIP = 16.0, 20.0
DEFAULT_CLIP = 18.0
LONG_VIDEO_THRESHOLD = 90.0
INTRO_SKIP_SECONDS = 22.0
OUTRO_SKIP_SECONDS = 15.0
CRASH_AUDIO_HIGH_PASS_HZ = 1500
ENABLE_SFX = True
SFX_INTENSITY = 1.0
ORIGINAL_AUDIO_VOLUME = 0.28
DEFAULT_SFX_DENSITY = "hyper"
DEFAULT_MEME_STYLE = "real"
MEME_STYLES = {
    "real": "Real Viral Memes (Myinstants Pack)",
    "hybrid": "Hybrid Real + Cartoon",
    "synthetic": "Synthetic Cartoon Only",
}
SFX_DENSITY_MODES = {
    "ultra": "Ultra-Dense (17 Real Meme Cues per Crash)",
    "hyper": "Kids Hyper-Fun (8-10 SFX per clip)",
    "standard": "Standard (4 SFX per clip)",
    "off": "Off",
}
SHORTS_MODES = {
    "viral": {"label": "Viral Single Crash (18-25s)", "max_seconds": 25.0},
    "classic59": {"label": "Classic Shorts Max (59s Multi-Crash)", "max_seconds": 59.0},
    "extended179": {"label": "Extended Shorts Max (179s / 3m)", "max_seconds": 179.0},
}
RENDER_MODES = {
    "1080x1920 @ 60fps (Full HD)": (1080, 1920, 60),
    "720x1280 @ 30fps (Fast Preview)": (720, 1280, 30),
}
YOLO_CLASSES = (2, 3, 5, 6, 7)  # COCO car, motorcycle, bus, train, truck
YOLO_CONFIDENCE = 0.27

PRESETS = {
    "action": {"shake": 16, "zoom": 1.04, "caption": "WAIT FOR IT!", "accent": "#ffed38"},
    "comic": {"shake": 11, "zoom": 1.04, "caption": "OH NO!", "accent": "#ff6542"},
    "clean": {"shake": 0, "zoom": 1.0, "caption": "", "accent": "#ffffff"},
}


def font_path() -> Path:
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "impact.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ROOT / "assets" / "DejaVuSans-Bold.ttf",
    ]
    candidates.extend(Path("/usr/share/fonts").glob("**/*Bold*.ttf"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Pillow ships a usable font in many environments; default bitmap font is
    # handled by renderer if a scalable font cannot be located.
    try:
        import PIL
        bundled = Path(PIL.__file__).parent / "fonts" / "DejaVuSans.ttf"
        if bundled.is_file():
            return bundled
    except ImportError:
        pass
    return Path("")


def ensure_dirs() -> None:
    for directory in (INPUTS, OUTPUTS, MODELS):
        directory.mkdir(parents=True, exist_ok=True)
