"""Deterministic, royalty-free synthesized cartoon effects and custom file lookup."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from config import ROOT

SFX_DIR = ROOT / "assets" / "sfx"
SAMPLE_RATE = 44100
# Optional brief examples not part of the acquired 40 recordings; role equivalents.
OPTIONAL_EVENT_ALIASES = {'omg_wow': 'wow_voice', 'screaming_goat': 'goofy_scream',
                          'emotional_damage': 'mission_failed', 'fart_reverb': 'bruh_meme',
                          'cartoon_bite_chomp': 'bonk_meme'}
BUILTINS = ("cartoon_whistle_intro", "countdown_beeps", "police_siren",
            "train_horn_meme", "cartoon_scurry", "boing_spring", "slip_peel",
            "riser_suspense", "cartoon_boom", "metal_crunch_bonk",
            "funny_horn_honk", "wah_wah_trombone")
LEGACY_BUILTINS = {"boing_jump", "oh_no_glockenspiel"}


def _phase(frequency: np.ndarray) -> np.ndarray:
    return 2 * np.pi * np.cumsum(frequency) / SAMPLE_RATE


def _fade(signal: np.ndarray, attack: float = 0.015, release: float = 0.1) -> np.ndarray:
    n = len(signal)
    fade_in = min(n, round(attack * SAMPLE_RATE))
    fade_out = min(n, round(release * SAMPLE_RATE))
    envelope = np.ones(n)
    if fade_in:
        envelope[:fade_in] = np.linspace(0, 1, fade_in)
    if fade_out:
        envelope[-fade_out:] *= np.linspace(1, 0, fade_out)
    return signal * envelope


def _siren() -> np.ndarray:
    t = np.arange(round(1.6 * SAMPLE_RATE)) / SAMPLE_RATE
    frequency = 925 + 275 * np.sin(2 * np.pi * 4.0 * t - np.pi / 2)
    phase = _phase(frequency)
    return _fade((np.sin(phase) + 0.22 * np.sin(2 * phase)) * 0.68, release=0.08)


def _boing() -> np.ndarray:
    t = np.arange(round(0.6 * SAMPLE_RATE)) / SAMPLE_RATE
    frequency = 250 * (950 / 250) ** (t / 0.6) * (1 + 0.04 * np.sin(2 * np.pi * 13 * t))
    phase = _phase(frequency)
    return _fade((np.sin(phase) + 0.3 * np.sin(2 * phase)) * np.exp(-2.1 * t), release=0.08)


def _riser() -> np.ndarray:
    t = np.arange(round(1.6 * SAMPLE_RATE)) / SAMPLE_RATE
    progress = t / 1.6
    phase = _phase(150 * (1400 / 150) ** progress)
    rng = np.random.default_rng(8103)
    noise = sosfilt(butter(2, 3400, fs=SAMPLE_RATE, output="sos"), rng.standard_normal(len(t)))
    roll = np.sin(2 * np.pi * 24 * t) * np.sin(_phase(90 + 40 * progress))
    return _fade((0.32 * np.sin(phase) + 0.12 * noise + 0.12 * roll) * progress ** 1.6,
                 release=0.025)


def _boom() -> np.ndarray:
    t = np.arange(round(1.3 * SAMPLE_RATE)) / SAMPLE_RATE
    phase = _phase(120 * (30 / 120) ** (t / 1.3))
    rng = np.random.default_rng(923)
    noise = sosfilt(butter(4, 4400, fs=SAMPLE_RATE, output="sos"), rng.standard_normal(len(t)))
    return _fade(0.72 * np.sin(phase) * np.exp(-2.5 * t) +
                 0.35 * noise * np.exp(-8 * t), attack=0.001, release=0.08)


def _oh_no() -> np.ndarray:
    duration = 1.2
    t = np.arange(round(duration * SAMPLE_RATE)) / SAMPLE_RATE
    signal = np.zeros_like(t)
    # G5, F#5, F5, E5: percussive, descending comedy sting.
    for index, frequency in enumerate((783.99, 739.99, 698.46, 659.25)):
        start = round(index * 0.28 * SAMPLE_RATE)
        end = min(len(t), start + round(0.35 * SAMPLE_RATE))
        local = np.arange(end - start) / SAMPLE_RATE
        signal[start:end] += (np.sin(2 * np.pi * frequency * local) +
                              0.32 * np.sin(2 * np.pi * 2.01 * frequency * local)) * np.exp(-10 * local)
    return _fade(signal * 0.7, attack=0.001, release=0.08)


def _whistle() -> np.ndarray:
    t = np.arange(round(0.7 * SAMPLE_RATE)) / SAMPLE_RATE
    frequency = np.interp(t, [0, 0.35, 0.7], [400, 1600, 700])
    phase = _phase(frequency)
    return _fade(0.75 * np.sin(phase) + 0.15 * np.sin(2 * phase), release=0.04)


def _countdown() -> np.ndarray:
    t = np.arange(round(1.5 * SAMPLE_RATE)) / SAMPLE_RATE
    signal = np.zeros_like(t)
    for index, frequency in enumerate((880, 880, 880, 1760)):
        start = round(index * 0.35 * SAMPLE_RATE)
        n = min(round((0.15 if index < 3 else 0.42) * SAMPLE_RATE), len(t) - start)
        local = np.arange(n) / SAMPLE_RATE
        signal[start:start + n] += 0.65 * (np.sin(2 * np.pi * frequency * local) +
                                            0.18 * np.sin(4 * np.pi * frequency * local)) * np.exp(-10 * local)
    return _fade(signal, attack=0.002, release=0.04)


def _horn() -> np.ndarray:
    t = np.arange(round(1.2 * SAMPLE_RATE)) / SAMPLE_RATE
    fundamental = sum(np.sin(2 * np.pi * frequency * t) for frequency in (220, 277, 330)) / 3
    brassy = np.tanh(2.8 * (fundamental + 0.25 * np.sin(2 * np.pi * 440 * t)))
    return _fade(0.75 * brassy * (0.8 + 0.2 * np.sin(2 * np.pi * 7 * t)),
                 attack=0.035, release=0.18)


def _scurry() -> np.ndarray:
    signal = np.zeros(round(1.2 * SAMPLE_RATE))
    for index in range(14):
        start = round(index * SAMPLE_RATE / 12)
        n = min(round(0.12 * SAMPLE_RATE), len(signal) - start)
        local = np.arange(n) / SAMPLE_RATE
        frequency = (1046.5, 1318.5, 1568.0)[index % 3]
        signal[start:start + n] += (np.sin(2 * np.pi * frequency * local) +
                                    0.3 * np.sin(2 * np.pi * 2 * frequency * local)) * np.exp(-28 * local)
    return _fade(signal * 0.7, attack=0.002, release=0.05)


def _spring() -> np.ndarray:
    t = np.arange(round(0.65 * SAMPLE_RATE)) / SAMPLE_RATE
    frequency = 220 * (900 / 220) ** (t / 0.65) * (1 + 0.09 * np.sin(2 * np.pi * 17 * t))
    phase = _phase(frequency)
    return _fade((np.sin(phase) + 0.28 * np.sin(2 * phase)) * np.exp(-1.5 * t),
                 release=0.07)


def _slip() -> np.ndarray:
    t = np.arange(round(0.6 * SAMPLE_RATE)) / SAMPLE_RATE
    frequency = np.interp(t, [0, 0.3, 0.6], [1800, 300, 1500])
    phase = _phase(frequency)
    return _fade(0.8 * np.sin(phase) + 0.2 * np.sin(2 * phase), release=0.055)


def _bonk() -> np.ndarray:
    t = np.arange(round(0.8 * SAMPLE_RATE)) / SAMPLE_RATE
    rng = np.random.default_rng(90210)
    rattle = sosfilt(butter(2, [2500, 9000], btype="bandpass", fs=SAMPLE_RATE,
                            output="sos"), rng.standard_normal(len(t)))
    return _fade((0.48 * np.sin(2 * np.pi * 520 * t) +
                  0.42 * np.sin(2 * np.pi * 840 * t)) * np.exp(-11 * t) +
                 0.18 * rattle * np.exp(-15 * t), attack=0.001, release=0.06)


def _honk() -> np.ndarray:
    t = np.arange(round(0.7 * SAMPLE_RATE)) / SAMPLE_RATE
    signal = np.zeros_like(t)
    for start_time in (0, 0.35):
        start = round(start_time * SAMPLE_RATE)
        n = min(round(0.27 * SAMPLE_RATE), len(t) - start)
        local = np.arange(n) / SAMPLE_RATE
        phase = 2 * np.pi * (380 * local + 8 * local * local)
        signal[start:start + n] += np.tanh(2.0 * (np.sin(phase) + 0.4 * np.sin(3 * phase))) * np.exp(-4 * local)
    return _fade(signal * 0.8, release=0.06)


def _trombone() -> np.ndarray:
    signal = np.zeros(round(1.5 * SAMPLE_RATE))
    for index, frequency in enumerate((293.66, 277.18, 261.63, 246.94)):
        start = round(index * 0.34 * SAMPLE_RATE)
        n = min(round((0.34 if index < 3 else 0.5) * SAMPLE_RATE), len(signal) - start)
        local = np.arange(n) / SAMPLE_RATE
        vibrato = frequency * (1 + 0.018 * np.sin(2 * np.pi * 5 * local))
        phase = _phase(vibrato)
        signal[start:start + n] += (np.sin(phase) + 0.4 * np.sin(2 * phase) +
                                    0.18 * np.sin(3 * phase)) * np.exp(-2 * local)
    return _fade(0.65 * np.tanh(signal), attack=0.008, release=0.16)


def ensure_sfx_pack() -> dict[str, Path]:
    """Create missing WAVs and refresh outdated generated effect durations."""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    generators = dict(zip(BUILTINS, (_whistle, _countdown, _siren, _horn, _scurry,
                                     _spring, _slip, _riser, _boom, _bonk, _honk,
                                     _trombone)))
    durations = (0.7, 1.5, 1.6, 1.2, 1.2, 0.65, 0.6, 1.6, 1.3, 0.8, 0.7, 1.5)
    for (name, generator), duration in zip(generators.items(), durations):
        path = SFX_DIR / f"{name}.wav"
        if (not path.is_file() or path.stat().st_size == 0 or
                abs(sf.info(path).duration - duration) > 0.01):
            signal = generator()
            peak = max(float(np.max(np.abs(signal))), 1e-6)
            sf.write(path, np.clip(signal / peak * 0.88, -1, 1), SAMPLE_RATE, subtype="PCM_16")
    return {name: SFX_DIR / f"{name}.wav" for name in BUILTINS}


def custom_sfx_files() -> list[Path]:
    ensure_sfx_pack()
    direct = (path for path in SFX_DIR.iterdir() if path.is_file() and
              path.suffix.lower() in {".mp3", ".wav"} and
              path.stem not in BUILTINS and path.stem not in LEGACY_BUILTINS)
    extras = (SFX_DIR / "real_memes").glob("extra_*.wav")
    return sorted((*direct, *extras))


def resolve_custom_effect(name: str | Path | None) -> Path | None:
    if not name:
        return None
    path = Path(name)
    if not path.is_absolute():
        path = SFX_DIR / path
    path = path.resolve()
    allowed = {SFX_DIR.resolve(), (SFX_DIR / "real_memes").resolve()}
    if path.parent not in allowed or path.suffix.lower() not in {".mp3", ".wav"} or not path.is_file():
        raise ValueError("Custom SFX must be an existing .mp3 or .wav in assets/sfx/ or real_memes/.")
    return path


def real_meme_timeline(impact: float, duration: float, variant: int = 0,
                       density: str = "hyper") -> list[tuple[str, float, float]]:
    """Different recorded jokes for successive crashes in a compilation."""
    if density == "off":
        return []
    start = ("what_the_meme", "police_siren_real", "anime_wow_yay")[variant % 3]
    approach = "thomas_theme_horn" if variant % 2 == 0 else "boing_cartoon"
    impact_layer = "bonk_meme" if variant % 2 == 0 else "airhorn_mlg"
    ending = "sad_violin_fail" if variant % 2 == 0 else "bruh_meme"
    if density == "standard":
        events = [(start, 0.2, 0.75), ("oh_no_voice", max(0, impact - 1.8), 0.8),
                  ("vine_boom", impact, 1.0), (ending, impact + 1.1, 0.8)]
    elif density == "ultra":
        events = [(start, 0.2, 0.55), ("record_scratch", 1.2, 0.35),
                  ("cartoon_run_scurry", 2.1, 0.45), ("mario_jump", 3.6, 0.4),
                  (approach, 5.2, 0.5), ("windows_error", max(5.5, impact - 6), 0.35),
                  ("among_us_sus", max(6.5, impact - 4.5), 0.35),
                  ("dramatic_gasp", max(7, impact - 2.7), 0.45),
                  ("oh_no_voice", max(7.5, impact - 1.7), 0.6),
                  ("cartoon_slip", max(8, impact - 0.8), 0.35),
                  ("vine_boom", impact, 0.8), ("bonk_meme", impact, 0.55),
                  ("metal_pipe", impact + 0.16, 0.35),
                  ("goofy_scream", impact + 0.4, 0.45),
                  ("cartoon_pop", impact + 0.85, 0.3),
                  ("baby_laugh", impact + 1.1, 0.45),
                  (ending, impact + 2.2, 0.45)]
    else:
        events = [(start, 0.2, 0.7), ("cartoon_run_scurry", 2.0, 0.65),
                  (approach, 4.5, 0.8),
                  ("oh_no_voice", max(0, impact - 1.8), 0.85),
                  ("cartoon_slip", max(0, impact - 1.8), 0.6),
                  ("vine_boom", impact, 1.0), (impact_layer, impact, 0.75),
                  ("goofy_scream", impact + 0.35, 0.7),
                  ("baby_laugh", impact + 1.1, 0.75),
                  (ending, impact + 2.3, 0.7)]
    return [(name, at, gain) for name, at, gain in events if 0 <= at < duration]


def director_meme_timeline(selected: dict[str, str], impact: float, duration: float,
                            density: str = "hyper", events: dict | None = None) -> list[tuple[str, float, float]]:
    """Turn the director's nine validated recorded cues into a crash-locked timeline."""
    if density == "off":
        return []
    e = events or {}
    schedule = (("intro", e.get("launch", .2), 0.6),
                 ("runup_1", e.get("bump", 3), .5),
                 ("runup_2", e.get("jump", 6), .45),
                 ("pre_crash", e.get("panic", impact-1.1), .65),
                 ("impact", e.get("impact", impact), .85),
                 ("impact_layer", e.get("impact", impact), .55),
                 ("mid_air", e.get("secondary", impact+.5), .5),
                 ("aftermath_1", e.get("aftermath", impact+1.6), .5))
    return [(selected[role][:-4], at, gain) for role, at, gain in schedule
             if role in selected and selected[role].endswith(".wav") and 0 <= at < duration]


def physics_meme_timeline(events: dict, duration: float, variant: int = 0) -> list[tuple[str, float, float]]:
    """Cute and impact layers locked to detected video/audio sub-peaks."""
    choices = (("turbo_whoosh", "yippee_cute", "cartoon_run_scurry"),
               ("squeaky_toy", "pop_bubble", "duck_quack", "pikachu_cute"),
               ("spring_jump", "mario_jump"), ("huh_cat", "oh_no_voice"),
               ("bonk_meme", "taco_bell_bong"), ("goofy_scream", "roblox_oof"),
               ("cute_giggle", "baby_laugh", "sad_violin_fail"))
    keys = ("launch", "bump", "jump", "panic", "impact", "secondary", "aftermath")
    cues = [(names[variant % len(names)], events[key], .45) for key, names in zip(keys, choices)
            if key in events and 0 <= events[key] < duration]
    for key, name in (("launch", "yippee_cute"),
                      ("jump", OPTIONAL_EVENT_ALIASES['omg_wow']),
                      ("panic", "crowd_gasp"),
                      ("panic", "tire_screech"), ("impact", "vine_boom"),
                      ("secondary", "roblox_oof")):
        if key in events and 0 <= events[key] < duration:
            cues.append((name, events[key], .38))
    return cues


REAL_FALLBACKS = {
    "baby_laugh": "funny_horn_honk", "oh_no_voice": "wah_wah_trombone",
    "bonk_meme": "metal_crunch_bonk", "vine_boom": "cartoon_boom",
    "cartoon_slip": "slip_peel", "boing_cartoon": "boing_spring",
    "airhorn_mlg": "train_horn_meme", "thomas_theme_horn": "train_horn_meme",
    "police_siren_real": "police_siren", "cartoon_run_scurry": "cartoon_scurry",
    "what_the_meme": "cartoon_whistle_intro", "goofy_scream": "slip_peel",
    "bruh_meme": "wah_wah_trombone", "sad_violin_fail": "wah_wah_trombone",
    "anime_wow_yay": "countdown_beeps",
}
