"""Procedural mid/high-forward music beds with chapter switching and VO ducking."""
from __future__ import annotations

import math
import random
import wave
from pathlib import Path

import numpy as np
from pydub import AudioSegment

SAMPLE_RATE = 48000
LIBRARY = Path(__file__).resolve().parents[1] / "assets" / "bgm_library"
TRACKS = {
    "industrial": ("01_industrial_drive_108bpm.wav", 108, (220.00, 174.61, 261.63, 196.00), "minor"),
    "precision": ("02_precision_tech_96bpm.wav", 96, (146.83, 196.00, 130.81, 220.00), "seventh"),
    "craft": ("03_craftsman_groove_88bpm.wav", 88, (146.83, 196.00, 220.00, 164.81), "major"),
    "foundry": ("04_heavy_foundry_114bpm.wav", 114, (110.00, 130.81, 146.83, 98.00), "minor"),
    "mystery": ("05_blueprint_mystery_90bpm.wav", 90, (146.83, 110.00, 130.81, 164.81), "minor"),
    "triumph": ("06_triumph_finale_112bpm.wav", 112, (261.63, 196.00, 220.00, 174.61), "major"),
}


def ensure_bgm_library(library: Path = LIBRARY) -> list[Path]:
    library.mkdir(parents=True, exist_ok=True)
    for style, (filename, bpm, roots, mood) in TRACKS.items():
        path = library / filename
        if not path.exists() or (wave.open(str(path), "rb").getnframes() < 89 * SAMPLE_RATE):
            _compose_track(path, style, bpm, roots, mood)
    return sorted([*library.glob("*.wav"), *library.glob("*.mp3")])


def _compose_track(path: Path, style: str, bpm: int, roots: tuple[float, ...], mood: str) -> None:
    duration = 90.0
    n = int(SAMPLE_RATE * duration)
    mix = np.zeros(n, dtype=np.float64)
    rng = np.random.default_rng(sum(map(ord, style)))
    beat = 60.0 / bpm
    chord_intervals = (0, 3, 7, 10) if mood == "seventh" else ((0, 4, 7, 11) if style == "triumph" else (0, 3, 7, 10))
    def add(start_sec: float, signal: np.ndarray, gain: float, pan: float = 0.0) -> None:
        begin = max(0, int(start_sec * SAMPLE_RATE))
        end = min(n, begin + len(signal))
        if end <= begin:
            return
        sig = signal[:end-begin] * gain
        mix[begin:end] += sig
    bars = math.ceil(duration / (4 * beat))
    for bar in range(bars):
        root = roots[bar % len(roots)]
        start = bar * 4 * beat
        length = min(4*beat, duration-start)
        size = int(length*SAMPLE_RATE)
        t = np.arange(size)/SAMPLE_RATE
        chord = np.zeros(size)
        for interval in chord_intervals:
            f = root * 2**(interval/12)
            chord += np.sin(2*np.pi*f*t) + .28*np.sin(2*np.pi*f*2*t)
        chord /= len(chord_intervals)*1.28
        env = np.minimum(1, t*3) * np.minimum(1, (length-t)*3)
        add(start, chord*env, .11, -.18)
        for step in range(8):
            at = start + step*beat/2
            local_root = root * (2 if step % 4 else 1)
            pluck_n = int(.22*SAMPLE_RATE)
            pt = np.arange(pluck_n)/SAMPLE_RATE
            f = local_root * (1 if style in ("craft", "mystery") else (1.5 if step%2 else 2))
            pluck = (np.sin(2*np.pi*f*pt)+.24*np.sin(2*np.pi*f*2.01*pt))*np.exp(-pt*15)
            add(at, pluck, .24 if style in ("craft", "precision") else .19, .2 if step%2 else -.2)
        # Kick 60->130Hz pitch sweep with audible upper transient.
        for b in range(4):
            at = start+b*beat
            d = .22; kt = np.arange(int(d*SAMPLE_RATE))/SAMPLE_RATE
            phase = 2*np.pi*(62*kt + 34/(2*d)*kt**2)
            kick = (np.sin(phase)+.12*np.sin(2*phase))*np.exp(-kt*18)
            add(at, kick, .34, 0)
            if b in (1, 3):
                d=.16; st=np.arange(int(d*SAMPLE_RATE))/SAMPLE_RATE
                noise=rng.normal(0,1,len(st)); noise=np.concatenate(([noise[0]],np.diff(noise)))
                snare=(noise*.7+.3*np.sin(2*np.pi*185*st))*np.exp(-st*22)
                add(at, snare, .19, .06)
            for eighth in (0,1):
                hat_t=np.arange(int(.045*SAMPLE_RATE))/SAMPLE_RATE
                hat=rng.normal(0,1,len(hat_t)); hat=np.concatenate(([hat[0]],np.diff(hat)))
                add(at+eighth*beat/2, hat*np.exp(-hat_t*65), .045, .1)
        # Distinct midrange lead motif; octave and contour shift by style.
        scale = (0, 3, 7, 10, 12, 10, 7, 3) if mood != "major" else (0, 4, 7, 11, 12, 11, 7, 4)
        for step, semitone in enumerate(scale):
            at = start + step*beat/2
            f = min(1800, root*2**(semitone/12)*(2 if step%3 else 3))
            d=.24; lt=np.arange(int(d*SAMPLE_RATE))/SAMPLE_RATE
            lead=(np.sin(2*np.pi*f*lt)+.32*np.sin(2*np.pi*2*f*lt)+.12*np.sin(2*np.pi*3*f*lt))*np.exp(-lt*8)
            add(at, lead, .11, -.35 if step%2 else .35)
    # 15ms edge fades, then normalize stereo RMS to -12 dBFS.
    fade = int(.015*SAMPLE_RATE)
    mix[:fade] *= np.linspace(0,1,fade)
    mix[-fade:] *= np.linspace(1,0,fade)
    rms = np.sqrt(np.mean(mix**2))
    mix *= (10**(-12/20))/max(rms, 1e-12)
    mix = np.clip(mix, -.98, .98)
    pcm = (np.repeat(mix[:,None],2,axis=1)*32767).astype(np.int16)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2); out.setsampwidth(2); out.setframerate(SAMPLE_RATE); out.writeframes(pcm.tobytes())


def compose_dynamic_bgm(project_dir: Path, duration_ms: int, timeline: list[dict],
                        hook_duration: float = 0.0, niche_mode: str = "engineering_machining",
                        bgm_style: str = "dynamic_multi") -> AudioSegment:
    if bgm_style == "craftsman":
        bgm_style = "craft"
    if bgm_style not in (*TRACKS, "dynamic_multi", "none"):
        raise ValueError(f"Unsupported bgm_style: {bgm_style}")
    if bgm_style == "none":
        return AudioSegment.silent(duration=duration_ms, frame_rate=48000).set_channels(2)
    files = ensure_bgm_library()
    builtins = {key: LIBRARY / value[0] for key, value in TRACKS.items()}
    extras = [path for path in files if path.name not in {p.name for p in builtins.values()}
              and not path.name.startswith(("01_", "02_", "03_", "04_", "05_", "06_"))]
    chapter_tracks = []
    for index, item in enumerate(timeline):
        chapter = int(item.get("chapter", index+1))
        style = ("industrial", "precision", "mystery", "foundry", "triumph", "craft")[(chapter-1)%6]
        if niche_mode == "woodworking_bonsai" and chapter in (2,3) and bgm_style == "dynamic_multi":
            style = "craft"
        if bgm_style != "dynamic_multi":
            style = bgm_style
        path = random.Random(20261114+chapter).choice(extras) if extras and chapter % 4 == 0 and bgm_style == "dynamic_multi" else builtins[style]
        track = AudioSegment.from_file(path).set_frame_rate(48000).set_channels(2)
        chapter_tracks.append((max(0, round(item["start_sec"]*1000)), _tile(track, duration_ms)))
    if not chapter_tracks:
        path = builtins["industrial"]
        chapter_tracks = [(0, _tile(AudioSegment.from_file(path).set_frame_rate(48000).set_channels(2), duration_ms))]
    elif chapter_tracks[0][0] > 0:
        chapter_tracks.insert(0, (0, chapter_tracks[0][1]))
    bgm = AudioSegment.silent(duration=duration_ms, frame_rate=48000).set_channels(2)
    for index, (start, track) in enumerate(chapter_tracks):
        end = chapter_tracks[index+1][0] if index+1 < len(chapter_tracks) else duration_ms
        part = track[:max(0,end-start)]
        if index+1 < len(chapter_tracks):
            part = part.fade_out(min(1200,len(part)))
        bgm = bgm.overlay(part, position=start)
        if index+1 < len(chapter_tracks):
            nxt = chapter_tracks[index+1][1][:1200].fade_in(1200)
            bgm = bgm.overlay(nxt, position=max(start,end-1200))
    # Audible absolute RMS targets: -14 dBFS gaps/hooks, -18.5 dBFS under VO.
    samples = np.asarray(bgm.get_array_of_samples(), dtype=np.float32).reshape(-1,2)
    envelope = np.full(duration_ms, -14.0, dtype=np.float32)
    voice_spans = [(max(0,round(x["start_sec"]*1000)), min(duration_ms,round(x["end_sec"]*1000))) for x in timeline]
    for start,end in voice_spans:
        if end > start: envelope[start:end] = -18.5
    ramp=300
    for point in np.flatnonzero(np.diff(envelope))+1:
        lo,hi=max(0,point-ramp//2),min(duration_ms,point+ramp//2)
        if hi>lo:
            before=envelope[point-1]; after=envelope[point]
            envelope[lo:hi]=np.linspace(before,after,hi-lo)
    # Normalize each source music layer via overall RMS, then apply target envelope.
    raw_rms=np.sqrt(np.mean(samples**2))
    normalized=samples*(10**(-12/20)*32768/max(raw_rms,1))
    gain=np.power(10,(np.repeat(envelope,48)[:len(samples)]+12)/20)[:,None]
    result=np.clip(normalized*gain,-32768,32767).astype(np.int16)
    return AudioSegment(result.tobytes(),frame_rate=48000,sample_width=2,channels=2)[:duration_ms]


def _tile(track: AudioSegment, duration_ms: int) -> AudioSegment:
    return (track*(duration_ms//max(1,len(track))+1))[:duration_ms]
