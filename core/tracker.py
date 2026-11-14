"""YOLO vehicle tracking with optical motion fallback and smoothed framing."""
from __future__ import annotations

import subprocess
import math
from pathlib import Path

import cv2
import numpy as np
from scipy.signal import savgol_filter

from config import (DEFAULT_FOV_SCALE, HEIGHT, WIDTH, MODEL_PATH, YOLO_CLASSES,
                    YOLO_CONFIDENCE)


def source_crop_width(source_width: int, source_height: int, output_width: int,
                      output_height: int, fov_scale: float, zoom: float = 1.0) -> int:
    """Full-height crop width, rounded to an even pixel for video encoders."""
    desired = source_height * output_width / output_height * fov_scale / zoom
    return max(2, min(source_width - source_width % 2, 2 * math.ceil(desired / 2)))


def foreground_height(output_width: int, crop_height: int, crop_width: int) -> int:
    """Even square-pixel foreground height, derived from the actual crop."""
    return max(2, 2 * round(output_width * crop_height / crop_width / 2))


def multi_object_center(boxes: np.ndarray, classes: np.ndarray,
                        motion: np.ndarray, frame_width: int = 640,
                        frame_height: int = 360) -> np.ndarray:
    """Blend the primary vehicle with the union of nearby vehicles and trains."""
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    centers = (boxes[:, :2] + boxes[:, 2:]) / 2
    scores = areas / (frame_width * frame_height) - np.linalg.norm(centers - motion, axis=1) / 2000
    primary = int(np.argmax(scores))
    distances = np.linalg.norm(centers - centers[primary], axis=1)
    nearby = (distances <= frame_width * 0.68) | (classes == 6)
    nearby[primary] = True
    if classes[primary] == 6 and np.any(classes == 2):
        cars = np.flatnonzero(classes == 2)
        nearby[cars[np.argmin(distances[cars])]] = True
    union = boxes[nearby]
    union_center = np.array([(union[:, 0].min() + union[:, 2].max()) / 2,
                             (union[:, 1].min() + union[:, 3].max()) / 2])
    return 0.25 * centers[primary] + 0.75 * union_center


def bootstrap_model() -> Path:
    """Download Ultralytics' official nano model on first run."""
    if MODEL_PATH.is_file() and MODEL_PATH.stat().st_size > 100_000:
        return MODEL_PATH
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    source = Path(model.ckpt_path)
    if source.resolve() != MODEL_PATH.resolve():
        import shutil
        shutil.copy2(source, MODEL_PATH)
    return MODEL_PATH


class AutoCropper:
    def __init__(self, width: int, height: int, detect_every: int = 6,
                 output_width: int = WIDTH, output_height: int = HEIGHT,
                 fov_scale: float = DEFAULT_FOV_SCALE):
        self.width, self.height = width, height
        self.output_width, self.output_height = output_width, output_height
        self.fov_scale = fov_scale
        self.detect_every = detect_every
        self.model = None
        self.device = "cpu"
        try:
            from ultralytics import YOLO
            import torch
            if not torch.cuda.is_available():
                torch.set_num_threads(min(4, torch.get_num_threads()))
            self.device = 0 if torch.cuda.is_available() else "cpu"
            self.model = YOLO(str(bootstrap_model()))
        except Exception as exc:
            print(f"YOLO unavailable ({exc}); using motion tracking.")

    def plan(self, source: Path, start: float, duration: float, fps: int) -> np.ndarray:
        """Track 640x360 frames at 10 fps, then interpolate/smooth to output fps."""
        sample_rate, sample_w, sample_h = 10, 640, 360
        frame_bytes = sample_w * sample_h * 3
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start),
                   "-i", str(source), "-t", str(duration), "-an", "-vf",
                   f"fps={sample_rate},scale={sample_w}:{sample_h}:flags=fast_bilinear",
                   "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        decoder = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, close_fds=True)
        centers: list[np.ndarray] = []
        prev_gray = None
        target = np.array([sample_w / 2, sample_h / 2], dtype=np.float64)
        try:
            assert decoder.stdout is not None
            while True:
                data = decoder.stdout.read(frame_bytes)
                if len(data) != frame_bytes:
                    break
                frame = np.frombuffer(data, np.uint8).reshape(sample_h, sample_w, 3)
                # Motion operates on a small sampled frame, never the full source.
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                strength = 0.0
                motion = target
                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    mask = (cv2.GaussianBlur(diff, (5, 5), 0) > 24).astype(np.uint8)
                    count = cv2.countNonZero(mask)
                    if count > 150:
                        moments = cv2.moments(mask, binaryImage=True)
                        motion = np.array([moments["m10"] / moments["m00"],
                                           moments["m01"] / moments["m00"]])
                        strength = min(1.0, count / 15000)
                prev_gray = gray
                if self.model is not None:
                    try:
                        detections = self.model.predict(frame, classes=list(YOLO_CLASSES),
                                                        conf=YOLO_CONFIDENCE, imgsz=320,
                                                        verbose=False, device=self.device)[0].boxes
                        boxes = detections.xyxy.cpu().numpy()
                        if len(boxes):
                            target = multi_object_center(boxes, detections.cls.cpu().numpy(),
                                                         motion, sample_w, sample_h)
                        elif strength > 0.03:
                            target = motion
                    except Exception as exc:
                        print(f"YOLO inference failed ({exc}); switching to motion tracking.")
                        self.model = None
                elif strength > 0.03:
                    target = motion
                centers.append(target.copy())
        finally:
            if decoder.stdout:
                decoder.stdout.close()
            if decoder.wait() != 0 and not centers:
                raise RuntimeError("Could not decode tracking frames.")
        total = max(1, round(duration * fps))
        if not centers:
            return np.tile([self.width / 2, self.height / 2], (total, 1))
        samples = np.asarray(centers, dtype=np.float64)
        samples[:, 0] *= self.width / sample_w
        samples[:, 1] *= self.height / sample_h
        if len(samples) >= 5:
            window = min(31, len(samples) if len(samples) % 2 else len(samples) - 1)
            samples = savgol_filter(samples, window_length=window, polyorder=2, axis=0)
            # Suppress sub-15px tracking tremors before fitting the camera path.
            for axis in range(2):
                anchor = samples[0, axis]
                for index in range(1, len(samples)):
                    if abs(samples[index, axis] - anchor) < 15:
                        samples[index, axis] = anchor
                    else:
                        anchor = samples[index, axis]
        output_times = np.arange(total) / fps
        sample_times = np.arange(len(samples)) / sample_rate
        trajectory = np.column_stack((np.interp(output_times, sample_times, samples[:, 0]),
                                      np.interp(output_times, sample_times, samples[:, 1])))
        crop_width = source_crop_width(self.width, self.height, self.output_width,
                                       self.output_height, self.fov_scale)
        trajectory[:, 0] = np.clip(trajectory[:, 0], crop_width / 2,
                                   self.width - crop_width / 2)
        return trajectory

    def crop(self, frame: np.ndarray, center: np.ndarray, zoom: float = 1.0) -> np.ndarray:
        height, width = frame.shape[:2]
        crop_w = source_crop_width(width, height, self.output_width,
                                   self.output_height, self.fov_scale, max(zoom, 1.0))
        crop_h = max(2, min(height, 2 * round(height / max(zoom, 1.0) / 2)))
        x = int(np.clip(center[0] - crop_w / 2, 0, width - crop_w))
        y = int(np.clip(center[1] - crop_h / 2, 0, height - crop_h))
        cropped = frame[y:y + crop_h, x:x + crop_w]
        fg_h = foreground_height(self.output_width, crop_h, crop_w)
        foreground = cv2.resize(cropped, (self.output_width, fg_h), interpolation=cv2.INTER_LANCZOS4)
        if self.fov_scale <= 1.0:
            return foreground
        background = cv2.resize(cropped, (self.output_width, self.output_height))
        background = cv2.GaussianBlur(background, (35, 35), 0)
        top = (self.output_height - fg_h) // 2
        background[top:top + fg_h] = foreground
        return background
