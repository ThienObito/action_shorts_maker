"""Source ambience extraction on the same timeline as the rendered documentary."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from pydub import AudioSegment

SOURCE_AUDIO_MODES = ("mute_all", "extract_env_sfx", "keep_raw_low")


def verify_and_clean_machine_sfx(vocals_wav: Path, sfx_wav: Path,
                                 output_wav: Path | None = None) -> dict:
    """Dynamically attenuate 300–2800 Hz when the Demucs vocal stem is loud.

    This measures *band attenuation*, not speech absence: Demucs vocals can
    contain music or machine noise; `other` can retain intelligible speech.
    Never set machine_sfx_verified solely from this proxy.
    """
    import soundfile as sf
    from scipy.signal import butter, sosfiltfilt

    vocals_wav, sfx_wav = Path(vocals_wav), Path(sfx_wav)
    output_wav = Path(output_wav) if output_wav else sfx_wav.with_name(sfx_wav.stem + "_cleaned.wav")
    vinfo, sinfo = sf.info(vocals_wav), sf.info(sfx_wav)
    if (vinfo.samplerate != sinfo.samplerate or vinfo.channels != sinfo.channels
            or abs(vinfo.frames - sinfo.frames) > int(sinfo.samplerate * .05)):
        raise ValueError("Demucs vocals and other must share sample rate, channels and duration")
    if output_wav.resolve() in (vocals_wav.resolve(), sfx_wav.resolve()):
        raise ValueError("Cleaning output must not overwrite original Demucs stems")
    sr, channels = sinfo.samplerate, sinfo.channels
    block = round(sr * .2)
    sos = butter(6, [300, 2800], btype="bandpass", fs=sr, output="sos")
    target = 1 - 10 ** (-14 / 20)
    last = 0.0
    active = count = 0
    before_energy = after_energy = 0.0
    machine_before = machine_after = 0.0
    with sf.SoundFile(vocals_wav) as voice, sf.SoundFile(sfx_wav) as original, \
            sf.SoundFile(output_wav, "w", samplerate=sr, channels=channels, subtype="PCM_16") as cleaned:
        while True:
            sound = original.read(block, dtype="float64", always_2d=True)
            if not len(sound):
                break
            vocal = voice.read(len(sound), dtype="float64", always_2d=True)
            rms = float(np.sqrt(np.mean(vocal**2))) if len(vocal) else 0.0
            selected = rms > 10 ** (-30 / 20)
            goal = target if selected else 0.0
            # Zero-phase filtering prevents subtracting a phase-shifted band
            # from *amplifying* the very frequencies targeted for removal.
            band = sosfiltfilt(sos, sound, axis=0)
            envelope = np.full(len(sound), goal, dtype=np.float64)
            if goal != last:
                ramp = min(len(sound), round(sr * .015))
                envelope[:ramp] = np.linspace(last, goal, ramp, endpoint=True)
            adjusted = np.clip(sound - band * envelope[:, None], -1, 1)
            cleaned.write(adjusted)
            last = goal
            count += 1
            if selected:
                active += 1
                frequencies = np.fft.rfftfreq(len(sound), 1 / sr)
                a = np.fft.rfft(sound, axis=0)
                b = np.fft.rfft(adjusted, axis=0)
                voice_band = (frequencies >= 300) & (frequencies <= 2800)
                machine_band = ((frequencies >= 120) & (frequencies <= 280)) | \
                               ((frequencies >= 3000) & (frequencies <= 6500))
                before_energy += float(np.sum(np.abs(a[voice_band]) ** 2))
                after_energy += float(np.sum(np.abs(b[voice_band]) ** 2))
                machine_before += float(np.sum(np.abs(a[machine_band]) ** 2))
                machine_after += float(np.sum(np.abs(b[machine_band]) ** 2))
    band_gain = 10 * np.log10((after_energy + 1e-15) / (before_energy + 1e-15))
    machine_gain = 10 * np.log10((machine_after + 1e-15) / (machine_before + 1e-15))
    return {"path": output_wav.name, "window_ms": 200, "vocal_activity_threshold_dbfs": -30,
            "vocal_activity_windows": active, "total_windows": count,
            "vocal_band_attenuation_db": round(float(band_gain), 2),
            "vocal_bleed_suppression_db": round(float(band_gain), 2),
            "machine_band_change_db": round(float(machine_gain), 2),
            "machine_sfx_verified": False, "speech_absence_verified": False,
            "limitation": "Proxy measures processing of 300-2800 Hz on Demucs-vocals-active windows; "
                          "it cannot detect or certify absence of intelligible speech or licensed music."}


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def _atempo(speed: float) -> str:
    if speed <= 0:
        raise ValueError("Audio speed must be positive")
    filters = []
    while speed > 2:
        filters.append("atempo=2")
        speed /= 2
    while speed < .5:
        filters.append("atempo=0.5")
        speed /= .5
    filters.append(f"atempo={speed:.8f}")
    return ",".join(filters)


def extract_machine_ambient_audio(source: Path, folder: Path, body_duration: float,
                                  hook_duration: float, mode: str) -> Path | None:
    """Extract aligned ambience; refuse to silently substitute unseparated source audio.

    Demucs 4-stem `other` is an approximation, NOT a guarantee of speech/music removal.
    Users must audition the resulting ambience before publishing.
    """
    if mode not in SOURCE_AUDIO_MODES:
        raise ValueError(f"Unsupported source_audio_mode: {mode}")
    if mode == "mute_all":
        return None
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                       "-of", "json", str(source)], check=True,
                                      capture_output=True, text=True).stdout)
    if not any(stream.get("codec_type") == "audio" for stream in probe["streams"]):
        raise ValueError(f"Source has no audio track for {mode}: {source}")
    duration = float(probe["format"]["duration"])
    prepared = folder / "source_ambient_aligned.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", "-af",
          f"{_atempo(duration/body_duration)},apad,atrim=duration={body_duration:.3f},"
          "highpass=f=120,lowpass=f=6500", "-ar", "48000", "-ac", "2", str(prepared)])
    selected = prepared
    if mode == "extract_env_sfx":
        stems = folder / "demucs_4stem"
        # Bounded CPU job; large projects must not multiply decoder/model workers.
        demucs_env = os.environ.copy()
        demucs_env.setdefault("OMP_NUM_THREADS", "2")
        demucs_env.setdefault("MKL_NUM_THREADS", "2")
        subprocess.run([sys.executable, "-m", "demucs.separate", "-n", "htdemucs",
                        "--device", "cpu", "--segment", "7", "--jobs", "1",
                        "--out", str(stems), str(prepared)],
                       check=True, capture_output=True, timeout=14400, env=demucs_env)
        selected = stems / "htdemucs" / prepared.stem / "other.wav"
        if not selected.is_file():
            raise FileNotFoundError(f"Demucs 4-stem other.wav missing: {selected}")
        vocals = selected.with_name('vocals.wav')
        cleaned = selected.with_name('other_cleaned.wav')
        if not vocals.is_file():
            raise FileNotFoundError('Demucs vocals stem is required for activity-based suppression')
        stats = verify_and_clean_machine_sfx(vocals, selected, cleaned)
        (folder / 'source_sfx_cleaning.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
        selected = cleaned
    filtered = folder / "source_ambient_filtered.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(selected), "-af", "highpass=f=120,lowpass=f=6500",
          "-ar", "48000", "-ac", "2", str(filtered)])
    return filtered


def overlay_ambient_mix(mix_path: Path, ambient_path: Path, voice_spans: list[dict],
                        hook_duration: float, mode: str) -> None:
    """Add ambience without reusing raw source music/voice unless explicitly requested."""
    mix = AudioSegment.from_file(mix_path).set_frame_rate(48000).set_channels(2)
    source = AudioSegment.from_file(ambient_path).set_frame_rate(48000).set_channels(2)
    hook_ms = round(hook_duration * 1000)
    if hook_ms:
        source = source[:hook_ms] + source
    source = (source + AudioSegment.silent(duration=max(0, len(mix)-len(source)), frame_rate=48000).set_channels(2))[:len(mix)]
    samples = np.asarray(source.get_array_of_samples(), dtype=np.float32).reshape(-1, 2)
    rms = float(np.sqrt(np.mean(samples**2)))
    if rms < 1:
        raise ValueError("Source ambience is silent")
    target = np.full(len(mix), -16.0 if mode == "extract_env_sfx" else -23.0, dtype=np.float32)
    for item in voice_spans:
        a = max(0, round(item["start_sec"]*1000)); b = min(len(target), round(item["end_sec"]*1000))
        target[a:b] = -21.0 if mode == "extract_env_sfx" else -28.0
    gains = (32768 / rms) * np.power(10, np.repeat(target, 48)[:len(samples)] / 20)
    bed = AudioSegment(np.clip(samples * gains[:, None], -32768, 32767).astype(np.int16).tobytes(),
                       frame_rate=48000, sample_width=2, channels=2)
    bed.export(mix_path.with_name("ambient_bed.wav"), format="wav")
    result = mix.overlay(bed)
    temporary = mix_path.with_name("master_audio_with_ambient_new.wav")
    result.export(temporary, format="wav")
    temporary.replace(mix_path)
    import shutil
    shutil.copy2(mix_path, mix_path.with_name("full_voiceover_mixed.wav"))
