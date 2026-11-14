"""Rank short action segments using audio transients and scene boundaries."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, find_peaks, sosfilt

from config import (CRASH_AUDIO_HIGH_PASS_HZ, DEFAULT_CLIP, INTRO_SKIP_SECONDS,
                    LONG_VIDEO_THRESHOLD, MAX_CLIP, MIN_CLIP, MODEL_PATH,
                    OUTRO_SKIP_SECONDS)


@dataclass
class Candidate:
    start: float
    duration: float
    peak: float
    score: float


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                              "-of", "json", str(path)], capture_output=True, text=True, check=True,
                            stdin=subprocess.DEVNULL, close_fds=True)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not any(s["codec_type"] == "video" for s in streams):
        raise ValueError("This file does not contain a video stream.")
    duration = float(data["format"].get("duration", 0))
    if duration <= 0:
        raise ValueError("Cannot determine video duration.")
    return {"duration": duration, "audio": any(s["codec_type"] == "audio" for s in streams)}


def audio_peaks(path: Path, duration: float) -> list[tuple[float, float]]:
    """High-frequency flux and transient contrast, not full-band music loudness."""
    rate, window = 22050, 2205
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
            "-f", "f32le", "pipe:1"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, close_fds=True)
    highpass = butter(4, CRASH_AUDIO_HIGH_PASS_HZ, btype="highpass", fs=rate, output="sos")
    state = np.zeros((highpass.shape[0], 2))
    energy, flux = [], []
    previous_spectrum = None
    try:
        assert process.stdout is not None
        while True:
            data = process.stdout.read(window * 4)
            if len(data) < window * 4:
                break
            samples = np.frombuffer(data, dtype="<f4")
            filtered, state = sosfilt(highpass, samples, zi=state)
            energy.append(float(np.sqrt(np.mean(filtered * filtered))))
            spectrum = abs(np.fft.rfft(samples * np.hanning(window)))
            spectrum[:round(CRASH_AUDIO_HIGH_PASS_HZ * window / rate)] = 0
            flux.append(float(np.maximum(spectrum - previous_spectrum, 0).sum())
                        if previous_spectrum is not None else 0.0)
            previous_spectrum = spectrum
    finally:
        if process.stdout:
            process.stdout.close()
        process.wait()
    if len(energy) < 4:
        return []
    energy = np.asarray(energy)
    flux = np.asarray(flux)
    # Local median suppresses steady cymbals/guitars while keeping crunch bursts.
    transient = np.maximum(0, energy - median_filter(energy, size=21))
    flux = np.maximum(0, flux - median_filter(flux, size=21))
    scale_e = max(float(np.quantile(transient, 0.95)), 1e-5)
    scale_f = max(float(np.quantile(flux, 0.95)), 1e-5)
    score = 0.65 * transient / scale_e + 0.35 * flux / scale_f
    indices, _ = find_peaks(score, prominence=0.3, distance=9)
    return sorted(((float(i * window / rate), float(score[i])) for i in indices
                   if i * window / rate < duration), key=lambda event: event[1], reverse=True)


def visual_chaos(path: Path, duration: float) -> tuple[np.ndarray, np.ndarray]:
    """Stream 320x180/5fps frames, discount smooth camera motion, score debris changes."""
    width, height, fps = 320, 180, 5
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
               "-an", "-vf", f"fps={fps},scale={width}:{height}:flags=fast_bilinear",
               "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, close_fds=True)
    values = []
    previous = None
    frame_bytes = width * height
    try:
        assert process.stdout is not None
        while True:
            data = process.stdout.read(frame_bytes)
            if len(data) != frame_bytes:
                break
            frame = np.frombuffer(data, np.uint8).reshape(height, width)
            if previous is None:
                values.append(0.0)
            else:
                # Translation alignment removes smooth pans; spatially distributed
                # residual change catches wreckage/debris over more than one region.
                shift, _ = cv2.phaseCorrelate(previous.astype(np.float32), frame.astype(np.float32))
                matrix = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
                aligned = cv2.warpAffine(previous, matrix, (width, height))
                # Ignore lower overlays (like/subscribe) and side borders.
                difference = cv2.absdiff(frame[16:145, 12:-12], aligned[16:145, 12:-12])
                cells = cv2.resize(difference, (16, 9), interpolation=cv2.INTER_AREA)
                values.append(float(0.7 * np.mean(difference) +
                                    0.3 * np.mean(np.minimum(cells, 45))))
            previous = frame
    finally:
        if process.stdout:
            process.stdout.close()
        if process.wait() != 0 and not values:
            raise RuntimeError("Could not decode visual crash evidence")
    raw = np.asarray(values)
    # A crash is a spike relative to its surrounding camera movement, not a
    # constantly moving portal/saw. Ignore extreme full-frame cuts.
    baseline = median_filter(raw, size=31)
    residual = np.maximum(0, raw - baseline)
    residual[raw > 55] *= 0.25
    return np.arange(len(raw)) / fps, residual


@lru_cache(maxsize=1)
def _vehicle_model():
    from ultralytics import YOLO
    return YOLO(str(MODEL_PATH)) if MODEL_PATH.is_file() else None


def vehicle_evidence(path: Path, timestamp: float) -> bool:
    """Require a vehicle on two approach frames; wreckage itself may evade YOLO."""
    model = _vehicle_model()
    if model is None:
        raise RuntimeError("YOLO model required to verify a crash; run --bootstrap")
    capture = cv2.VideoCapture(str(path))
    try:
        hits = 0
        for offset in (1.0, 2.0, 3.0):
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0, timestamp - offset) * 1000)
            success, frame = capture.read()
            if not success:
                continue
            result = model.predict(cv2.resize(frame, (320, 180)), imgsz=320,
                                   classes=[2, 3, 5, 6, 7], conf=0.18,
                                   verbose=False, device="cpu")[0]
            hits += bool(len(result.boxes))
            if hits >= 2:
                return True
        return False
    finally:
        capture.release()


def portal_frame(path: Path, timestamp: float) -> bool:
    """Reject a dominant red spiral encircled by the yellow 95 portal rim."""
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        return False
    small = cv2.resize(frame, (320, 180))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    circles = cv2.HoughCircles(cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 5),
                               cv2.HOUGH_GRADIENT, 1, 60, param1=80, param2=28,
                               minRadius=25, maxRadius=140)
    if circles is None:
        return False
    yy, xx = np.ogrid[:180, :320]
    red = (((hsv[:, :, 0] < 13) | (hsv[:, :, 0] > 172)) &
           (hsv[:, :, 1] > 90)).astype(np.uint8)
    yellow = ((hsv[:, :, 0] >= 13) & (hsv[:, :, 0] < 44) &
              (hsv[:, :, 1] > 100)).astype(np.uint8)
    for x, y, radius in circles[0]:
        # The portal dominates the middle of the shot. Small red flames,
        # spinning saws and off-centre lava pools are not portals.
        if radius < 60 or not (80 <= x <= 240 and 30 <= y <= 145):
            continue
        distance = (xx - x) ** 2 + (yy - y) ** 2
        interior = distance < (0.62 * radius) ** 2
        rim = (distance > (0.66 * radius) ** 2) & (distance < (1.12 * radius) ** 2)
        if np.mean(red[interior]) > 0.55:
            return True
    return False


def scene_cuts(path: Path, start: float | None = None, end: float | None = None) -> list[float]:
    from scenedetect import ContentDetector, detect

    try:
        scenes = detect(str(path), ContentDetector(threshold=30.0), show_progress=False,
                        start_time=start, end_time=end)
        return [float(start.get_seconds()) for start, _ in scenes[1:]]
    except Exception as exc:
        print(f"Scene detection unavailable ({exc}); using audio peaks only.")
        return []


def segment_window(peak: float, duration: float, cuts: list[float]) -> tuple[float, float]:
    """Frame a crash with the full approach and aftermath, respecting file boundaries."""
    preceding = [cut for cut in cuts if peak - 15.0 <= cut <= peak - 11.0]
    start = min(preceding, key=lambda cut: abs(cut - (peak - 13.5))) if preceding else max(0.0, peak - 13.5)
    end = min(duration, peak + 4.5)
    target_min = min(MIN_CLIP, duration)
    if end - start < target_min:
        start = max(0.0, end - target_min)
        end = min(duration, max(end, start + target_min))
    if duration > MAX_CLIP and end - start > MAX_CLIP:
        # Limit run-up while retaining at most 4.5 seconds of aftermath.
        start = end - MAX_CLIP
    return round(start, 3), round(end, 3)


def rank_crash_events(peaks: list[tuple[float, float]], times: np.ndarray,
                      visual: np.ndarray, duration: float) -> list[Candidate]:
    """Cross-score audio and visual spikes before expensive vehicle verification."""
    protected = duration > LONG_VIDEO_THRESHOLD
    min_start = INTRO_SKIP_SECONDS if protected else 0.0
    max_end = duration - OUTRO_SKIP_SECONDS if protected else duration
    # Refine collision time within +/-2s where high-frequency transient and
    # motion spikes coincide; do not let a nearby portal/music hit hijack it.
    visual_scale = max(float(np.quantile(visual, 0.9)), 1e-4)
    candidates = []
    for rough, strength in peaks:
        if rough < min_start + 1 or rough + 4.5 > max_end:
            continue
        nearby = (times >= rough - 2) & (times <= rough + 2)
        if not nearby.any():
            continue
        indices = np.flatnonzero(nearby)
        matched_audio = np.asarray([max((s for t, s in peaks if abs(t - times[i]) <= 0.3), default=0)
                                    for i in indices])
        combined = matched_audio * np.minimum(visual[indices] / visual_scale, 5)
        best = indices[int(np.argmax(combined))]
        exact, chaos = float(times[best]), float(visual[best] / visual_scale)
        if chaos < 0.65 or combined.max() < 0.45:
            continue
        start, end = segment_window(exact, duration, [])
        if start < min_start:
            start = min_start
            end = max(end, start + MIN_CLIP)
        if start < min_start or end > max_end:
            continue
        candidates.append(Candidate(start, end - start, exact, float(combined.max())))
    candidates.sort(key=lambda clip: clip.score, reverse=True)
    # Nearby audio bursts frequently refine to the same 0.2s frame. Do not
    # seek/decode and run YOLO on the same false portal dozens of times.
    unique = []
    for clip in candidates:
        if all(abs(clip.peak - existing.peak) >= 3.0 for existing in unique):
            unique.append(clip)
    return unique


def find_candidates(path: Path, count: int = 3, length: float = DEFAULT_CLIP) -> list[Candidate]:
    metadata = probe(path)
    duration = metadata["duration"]
    if duration <= 0:
        return []
    if not metadata["audio"]:
        raise ValueError("Crash verification requires audio transients; no audio stream found.")
    peaks = audio_peaks(path, duration)
    times, visual = visual_chaos(path, duration)
    if not len(peaks) or not len(visual):
        raise ValueError("No crash evidence found in the source.")
    candidates = rank_crash_events(peaks, times, visual, duration)
    selected = []
    checked = 0
    for clip in candidates:
        if any(abs(clip.peak - other.peak) < 24 or
               not (clip.start + clip.duration <= other.start or
                    other.start + other.duration <= clip.start) for other in selected):
            continue
        if checked >= 40:
            break
        checked += 1
        if portal_frame(path, clip.peak):
            continue
        if not vehicle_evidence(path, clip.peak):
            continue
        selected.append(clip)
        if len(selected) >= count:
            break
    if not selected:
        raise ValueError("No visually verified vehicle crashes found; refusing to score intro or portal B-roll.")
    return selected
