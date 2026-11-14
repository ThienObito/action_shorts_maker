"""Detect required media tools and optional GPU encoder."""
from __future__ import annotations
import shutil, subprocess, sys
for name in ("ffmpeg", "ffprobe", "yt-dlp"):
    print(f"{name}: {shutil.which(name) or 'NOT IN PATH'}")
try:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
    print("h264_nvenc:", "available" if "h264_nvenc" in out.stdout else "unavailable")
except FileNotFoundError:
    print("h264_nvenc: unavailable (ffmpeg missing)")
print("Python:", sys.version.split()[0])
