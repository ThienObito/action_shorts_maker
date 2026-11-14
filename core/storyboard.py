"""Bounded 20 Hz physics/audio telemetry and six-frame storyboard."""
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.signal import find_peaks

from core.analyzer import Candidate


def micro_events(source: Path, clip: Candidate) -> dict:
    fps, w, h = 20, 160, 90
    command = ['ffmpeg', '-v', 'error', '-ss', str(clip.start), '-t', str(clip.duration),
               '-i', str(source), '-an', '-vf', f'fps={fps},scale={w}:{h}',
               '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, close_fds=True)
    flow, vertical, light = [], [], []
    previous = None
    try:
        while True:
            raw = process.stdout.read(w*h)
            if len(raw) != w*h:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(h, w)
            light.append(float(frame.mean()))
            if previous is None:
                flow.append(0.0); vertical.append(0.0)
            else:
                optical = cv2.calcOpticalFlowFarneback(previous, frame, None,
                                                       .5, 2, 11, 2, 5, 1.1, 0)
                speed = cv2.magnitude(optical[:,:,0], optical[:,:,1])
                flow.append(float(np.mean(speed)))
                vertical.append(float(np.mean(np.abs(optical[:,:,1]))))
            previous = frame.copy()
    finally:
        process.stdout.close()
        process.wait()
    if not flow:
        raise ValueError('No frames decoded for storyboard')
    audio = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(clip.start), '-t', str(clip.duration),
                            '-i', str(source), '-vn', '-ac', '1', '-ar', '8000',
                            '-f', 'f32le', 'pipe:1'], capture_output=True, check=True,
                           stdin=subprocess.DEVNULL, close_fds=True).stdout
    samples = np.frombuffer(audio, '<f4')
    bins = [float(np.sqrt(np.mean(samples[i:i+400]**2))) for i in range(0, len(samples)-399, 400)]
    motion = np.asarray(flow)
    envelope = np.interp(np.arange(len(motion)), np.arange(len(bins)), bins) if bins else np.zeros(len(motion))
    scaled_audio = envelope / max(float(np.quantile(envelope, .95)), 1e-6)
    scaled_motion = motion / max(float(np.quantile(motion, .95)), 1e-6)
    combined = .65*scaled_motion + .35*scaled_audio
    times = np.arange(len(motion)) / fps
    peak = clip.peak - clip.start
    def strongest(lo, hi, series, default):
        indices = np.flatnonzero((times >= lo) & (times <= hi))
        return round(float(times[indices[np.argmax(np.asarray(series)[indices])]]), 3) if len(indices) else round(default, 3)
    slope = np.maximum(0, np.diff(combined, prepend=combined[0]))
    launch = strongest(0.3, max(0.5, peak-3), slope, 1.0)
    bump = strongest(launch+0.4, max(launch+0.5, peak-2), combined, peak-3)
    jump = strongest(max(0, peak-4), peak-1.3, np.asarray(vertical)+np.abs(np.diff(light, prepend=light[0])), peak-2)
    secondary = strongest(peak+0.35, min(clip.duration-0.1, peak+0.9), combined, peak+0.5)
    if bins:
        audio_times = np.arange(len(bins)) / 20
        valid = np.flatnonzero((audio_times >= peak+0.35) & (audio_times <= peak+0.9))
        if len(valid):
            secondary = round(float(audio_times[valid[np.argmax(np.asarray(bins)[valid])]]), 3)
    return {'launch': launch, 'bump': bump, 'jump': jump,
            'panic': round(max(0, peak-1.1), 3), 'impact': round(peak, 3),
            'secondary': secondary, 'aftermath': round(min(clip.duration-0.1, peak+1.6), 3),
            'runup_speed_score': round(float(np.quantile(motion[times<peak], .85)) if np.any(times<peak) else 0, 3),
            'airborne_detected': bool(max(vertical or [0]) > np.median(vertical)*2.5+1),
            'collision_chaos_score': round(float(max(motion[max(0,int(peak*20)-3):int(peak*20)+5], default=0)), 3),
            'secondary_hit_offset': round(secondary-peak, 3)}


def contact_sheet(source: Path, clip: Candidate, output: Path) -> Path:
    labels = ('Lineup', 'Acceleration', 'Obstacle Approaching', 'Pre-Impact', 'Exact Crash', 'Wreckage')
    relative = (0.1, .35, max(.4, clip.peak-clip.start-2.5),
                max(.5, clip.peak-clip.start-.45), clip.peak-clip.start,
                min(clip.duration-.1, clip.peak-clip.start+.7))
    image = Image.new('RGB', (960, 570), '#101020')
    draw = ImageDraw.Draw(image)
    cap = cv2.VideoCapture(str(source))
    try:
        for index, (label, at) in enumerate(zip(labels, relative)):
            cap.set(cv2.CAP_PROP_POS_MSEC, (clip.start+at)*1000)
            ok, frame = cap.read()
            if not ok:
                continue
            frame = cv2.resize(frame, (320, 180))
            image.paste(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
                        ((index%3)*320, (index//3)*285))
            draw.text(((index%3)*320+8, (index//3)*285+183), f'{label} | {clip.start+at:.2f}s', fill='yellow')
    finally:
        cap.release()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, quality=85)
    return output
