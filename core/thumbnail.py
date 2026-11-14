"""Sharp crash-frame covers, generated only from the selected source footage."""
from __future__ import annotations

from pathlib import Path

import cv2
import math
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from config import font_path
from core.analyzer import Candidate


def collision_frame(source: Path, clip: Candidate) -> tuple[Image.Image, tuple[float, float]]:
    """Pick a sharp frame with local visual change within 0.8s of impact."""
    cap = cv2.VideoCapture(str(source))
    try:
        best = None
        previous = None
        for offset in np.arange(-0.8, 0.81, 0.2):
            cap.set(cv2.CAP_PROP_POS_MSEC, max(clip.start, clip.peak + float(offset)) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(small, cv2.CV_64F).var())
            motion = float(cv2.absdiff(small, previous).mean()) if previous is not None else 0
            score = np.log1p(sharpness) + 0.08 * motion - 0.35 * abs(offset)
            if best is None or score > best[0]:
                best = (score, frame.copy())
            previous = small
    finally:
        cap.release()
    if best is None:
        raise ValueError("Cannot decode collision frame for thumbnail")
    frame = best[1]
    # Localized motion/contrast identifies the visual center of the wreck.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    change = cv2.Laplacian(cv2.resize(gray, (32, 18)), cv2.CV_32F)
    y, x = np.unravel_index(np.argmax(cv2.GaussianBlur(np.abs(change), (5, 5), 0)), change.shape)
    point = ((x + 0.5) / 32, (y + 0.5) / 18)
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), point


def _cover(image: Image.Image, size: tuple[int, int], focus: tuple[float, float]) -> tuple[Image.Image, tuple[int, int]]:
    w, h = size
    scale = max(w / image.width, h / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    cx, cy = focus[0] * image.width, focus[1] * image.height
    left = int(np.clip(cx - w / 2, 0, image.width - w))
    top = int(np.clip(cy - h / 2, 0, image.height - h))
    return image.crop((left, top, left + w, top + h)), (int(cx - left), int(cy - top))


def _draw(image: Image.Image, target: tuple[int, int], text: str) -> Image.Image:
    w, h = image.size
    draw = ImageDraw.Draw(image)
    border = 20
    draw.rectangle((0, 0, w - 1, h - 1), outline="#fff000", width=border)
    draw.rectangle((border, border, w - border - 1, h - border - 1), outline="#ff203a", width=8)
    tx, ty = target
    radius = round(min(w, h) * 0.15)
    for angle in range(0, 360, 20):
        a = math.radians(angle)
        ox, oy = w//2, h//2
        draw.line((ox+math.cos(a)*min(w,h)*.42, oy+math.sin(a)*min(w,h)*.42,
                   ox+math.cos(a)*min(w,h)*.65, oy+math.sin(a)*min(w,h)*.65),
                  fill='#fff000', width=5)
    for spread, color, stroke in ((24, '#ff3322', 26), (12, '#fff000', 18), (0, '#f91b25', 10)):
        draw.ellipse((tx-radius-spread, ty-radius-spread, tx+radius+spread, ty+radius+spread),
                     outline=color, width=stroke)
    draw.ellipse((tx-radius, ty-radius, tx+radius, ty+radius), outline="#fff000", width=18)
    draw.ellipse((tx-radius-10, ty-radius-10, tx+radius+10, ty+radius+10), outline="#ff203a", width=10)
    start = (max(60, tx-radius*2), max(round(h*0.27), ty-radius*2))
    end = (tx-radius//2, ty-radius//2)
    for extra, color in ((16, '#111111'), (8, '#ffffff'), (0, '#ff203a')):
        draw.line((start, end), fill=color, width=35+extra)
    draw.polygon((end, (end[0]-65, end[1]-8), (end[0]-8, end[1]-65)), fill="#ff203a")
    words = text.upper().strip()
    font_size = round(w * 0.125)
    while True:
        font = ImageFont.truetype(str(font_path()), font_size)
        box = draw.textbbox((0, 0), words, font=font, stroke_width=11)
        if box[2] - box[0] <= w - 90 or font_size <= 30:
            break
        font_size -= 4
    x = (w - (box[2]-box[0])) // 2
    y = 50 if h < 1000 else 125
    draw.rounded_rectangle((max(22,x-25), y-12, min(w-22,x+box[2]+25), y+box[3]-box[1]+37),
                           radius=22, fill="#171526")
    # One tilted composited glyph group: a black extrusion and warm gradient fill.
    text_layer = Image.new('RGBA', (w, max(220, font_size*2)), (0,0,0,0))
    td = ImageDraw.Draw(text_layer)
    td.text((x+8,18), words, font=font, fill='#28111a', stroke_width=13, stroke_fill='#110b13')
    td.text((x,10), words, font=font, fill='#ffb300', stroke_width=7, stroke_fill='#ffffff')
    rotated = text_layer.rotate(4, Image.Resampling.BICUBIC, expand=False)
    image.alpha_composite(rotated, (0,y-10)) if image.mode == 'RGBA' else image.paste(rotated, (0,y-10), rotated)
    draw = ImageDraw.Draw(image)
    badge = "99.9% FAIL!"
    badge_font = ImageFont.truetype(str(font_path()), round(w*.065))
    bx, by = w//2, h-95
    draw.rounded_rectangle((bx-230,by-25,bx+230,by+72), radius=22, fill='#ed1937', outline='white', width=9)
    draw.text((bx,by+20),badge,anchor='mm',font=badge_font,fill='#fff000',stroke_width=4,stroke_fill='black')
    return image


def generate_thumbnails(source: Path, clip: Candidate, stem: Path, text: str) -> tuple[Path, Path]:
    if not 2 <= len(text.split()) <= 3:
        raise ValueError("Thumbnail text must contain 2-3 words")
    source_image, focus = collision_frame(source, clip)
    source_image = ImageEnhance.Color(source_image).enhance(1.50)
    source_image = ImageEnhance.Contrast(source_image).enhance(1.25)
    source_image = ImageEnhance.Sharpness(source_image).enhance(1.6)
    paths = []
    for label, size in (("vertical", (1080, 1920)), ("horizontal", (1280, 720))):
        image, point = _cover(source_image, size, focus)
        image = _draw(image, point, text)
        path = stem.parent / f"{stem.name}_thumb_{label}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "JPEG", quality=92, optimize=True)
        paths.append(path)
    return tuple(paths)


def generate_thumbnail(source: Path, clip: Candidate, output: Path, text: str) -> Path:
    """Backwards-compatible horizontal thumbnail helper."""
    _, horizontal = generate_thumbnails(source, clip, output.with_suffix(""), text)
    horizontal.replace(output)
    return output
