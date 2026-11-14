"""Crash selection must reject intro/outro and require visual vehicle evidence."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from config import INTRO_SKIP_SECONDS, MODEL_PATH, OUTRO_SKIP_SECONDS
from core.analyzer import audio_peaks, find_candidates, portal_frame, vehicle_evidence


class VerifiedCrashTests(unittest.TestCase):
    def test_high_frequency_burst_beats_steady_music_tone(self):
        rate = 22050
        t = np.arange(rate * 5) / rate
        signal = 0.45 * np.sin(2 * np.pi * 440 * t)
        rng = np.random.default_rng(42)
        signal[2 * rate:2 * rate + rate // 8] += 0.45 * rng.normal(size=rate // 8)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "music_and_crash.wav"
            sf.write(path, np.clip(signal, -1, 1), rate)
            peaks = audio_peaks(path, 5)
        self.assertTrue(peaks)
        self.assertAlmostEqual(peaks[0][0], 2.0, delta=0.3)

    def test_long_video_guardrails_and_visual_gate(self):
        times = np.arange(0, 480, 0.2)
        visual = np.zeros(len(times))
        visual[np.argmin(abs(times - 100))] = 3.0
        peaks = [(20.0, 10.0), (100.0, 2.0), (472.0, 10.0)]
        with (patch("core.analyzer.probe", return_value={"duration": 480.0, "audio": True}),
              patch("core.analyzer.audio_peaks", return_value=peaks),
              patch("core.analyzer.visual_chaos", return_value=(times, visual)),
              patch("core.analyzer.vehicle_evidence", return_value=True)):
            clips = find_candidates(Path("sample.mp4"), 3)
        self.assertEqual(len(clips), 1)
        self.assertGreaterEqual(clips[0].start, INTRO_SKIP_SECONDS)
        self.assertLessEqual(clips[0].start + clips[0].duration, 480 - OUTRO_SKIP_SECONDS)
        self.assertAlmostEqual(clips[0].peak, 100.0, places=1)

    def test_rejects_music_or_portal_without_vehicle(self):
        times = np.arange(0, 240, 0.2)
        visual = np.zeros(len(times))
        visual[np.argmin(abs(times - 120))] = 4.0
        with (patch("core.analyzer.probe", return_value={"duration": 240.0, "audio": True}),
              patch("core.analyzer.audio_peaks", return_value=[(120.0, 3.0)]),
              patch("core.analyzer.visual_chaos", return_value=(times, visual)),
              patch("core.analyzer.vehicle_evidence", return_value=False)):
            with self.assertRaisesRegex(ValueError, "No visually verified vehicle crashes"):
                find_candidates(Path("sample.mp4"), 1)

    def test_demo_source_never_uses_protected_zone(self):
        source = Path(__file__).resolve().parents[1] / "downloads" / "-brcT3E5Dcg.mp4"
        if not source.is_file():
            self.skipTest("Demo video not downloaded")
        from core.analyzer import probe
        self.assertGreater(probe(source)["duration"], 90)
        # The actual crash selection is covered by the separate integration run;
        # this keeps the quick unit suite independent of a 480-second decode.
        times = np.arange(0, 480, 0.2)
        visual = np.zeros(len(times))
        visual[np.argmin(abs(times - 100))] = 3.0
        with (patch("core.analyzer.audio_peaks", return_value=[(100.0, 2.0)]),
              patch("core.analyzer.visual_chaos", return_value=(times, visual)),
              patch("core.analyzer.vehicle_evidence", return_value=True)):
            clips = find_candidates(source, 1)
        self.assertTrue(all(clip.start >= INTRO_SKIP_SECONDS for clip in clips))

    def test_demo_portal_is_not_a_wreck(self):
        source = Path(__file__).resolve().parents[1] / "downloads" / "-brcT3E5Dcg.mp4"
        if not source.is_file():
            self.skipTest("Demo video not downloaded")
        self.assertTrue(portal_frame(source, 116.8))
        self.assertTrue(portal_frame(source, 126.4))
        self.assertFalse(portal_frame(source, 91.6))

    def test_demo_approach_has_vehicle_but_lineup_does_not_verify(self):
        source = Path(__file__).resolve().parents[1] / "downloads" / "-brcT3E5Dcg.mp4"
        if not source.is_file() or not MODEL_PATH.is_file():
            self.skipTest("Demo video or YOLO weights not downloaded")
        self.assertFalse(vehicle_evidence(source, 84.0))
        self.assertTrue(vehicle_evidence(source, 51.4))

    def test_portal_rejected_even_when_vehicle_is_nearby(self):
        times = np.arange(0, 240, 0.2)
        visual = np.zeros(len(times))
        visual[np.argmin(abs(times - 120))] = 4.0
        with (patch("core.analyzer.probe", return_value={"duration": 240.0, "audio": True}),
              patch("core.analyzer.audio_peaks", return_value=[(120.0, 3.0)]),
              patch("core.analyzer.visual_chaos", return_value=(times, visual)),
              patch("core.analyzer.portal_frame", return_value=True),
              patch("core.analyzer.vehicle_evidence") as vehicle):
            with self.assertRaisesRegex(ValueError, "No visually verified vehicle crashes"):
                find_candidates(Path("sample.mp4"), 1)
        vehicle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
