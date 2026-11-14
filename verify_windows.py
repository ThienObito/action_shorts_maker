"""Validate the Windows environment and application imports."""
from __future__ import annotations

import importlib
import subprocess
import sys

from config import INPUTS, MODEL_PATH, font_path


def main() -> None:
    modules = ("yt_dlp", "librosa", "numpy", "scipy", "cv2", "ultralytics",
               "scenedetect", "PIL", "gradio", "soundfile", "core.analyzer",
               "core.downloader", "core.tracker", "core.renderer", "core.sfx",
               "core.compiler", "app")
    for name in modules:
        importlib.import_module(name)
        print(f"OK import {name}")
    for program in ("ffmpeg", "ffprobe"):
        output = subprocess.run([program, "-version"], capture_output=True, text=True, check=True)
        print(output.stdout.splitlines()[0])
    print(f"Python: {sys.executable}")
    print(f"Font: {font_path()}")
    print(f"Model exists: {MODEL_PATH.is_file()}")
    print(f"Downloads: {INPUTS}")


if __name__ == "__main__":
    main()
