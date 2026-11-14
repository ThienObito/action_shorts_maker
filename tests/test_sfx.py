"""SFX assets and strict mode configuration regression tests."""
import unittest

import soundfile as sf

from config import ORIGINAL_AUDIO_VOLUME, SFX_DENSITY_MODES, SFX_INTENSITY, SHORTS_MODES
from core.renderer import audio_mix_filters, sfx_timeline
from core.sfx import BUILTINS, SAMPLE_RATE, ensure_sfx_pack, resolve_custom_effect


class SfxTests(unittest.TestCase):
    def test_builtin_pack_is_nonempty_44100hz(self):
        assets = ensure_sfx_pack()
        self.assertEqual(set(assets), set(BUILTINS))
        self.assertEqual(len(assets), 12)
        durations = (0.7, 1.5, 1.6, 1.2, 1.2, 0.65, 0.6, 1.6, 1.3, 0.8, 0.7, 1.5)
        for path, expected_duration in zip(assets.values(), durations):
            self.assertGreater(path.stat().st_size, 1000)
            self.assertEqual(sf.info(path).samplerate, SAMPLE_RATE)
            self.assertAlmostEqual(sf.info(path).duration, expected_duration, places=2)

    def test_hyper_timeline_and_standard_off(self):
        events = sfx_timeline(13.5, 18, "hyper")
        self.assertGreaterEqual(len(events), 10)
        self.assertEqual([name for name, _, _ in events if name in
                          {"cartoon_boom", "metal_crunch_bonk"}],
                         ["cartoon_boom", "metal_crunch_bonk"])
        self.assertEqual(len(sfx_timeline(13.5, 18, "standard")), 4)
        self.assertEqual(sfx_timeline(13.5, 18, "off"), [])
        self.assertEqual(SFX_DENSITY_MODES["hyper"], "Kids Hyper-Fun (8-10 SFX per clip)")

    def test_original_is_ducked_and_effect_mix_not_divided(self):
        self.assertEqual(ORIGINAL_AUDIO_VOLUME, 0.28)
        self.assertEqual(SFX_INTENSITY, 1.0)
        assets = ensure_sfx_pack()
        effects = [(assets[name], at, gain) for name, at, gain in sfx_timeline(13.5, 18)]
        graph = audio_mix_filters(True, effects, 3, 18)
        self.assertIn("equalizer=f=1200:t=q:w=1.2:g=-5,volume=0.2800", graph)
        self.assertIn(f"amix=inputs={len(effects) + 1}:duration=first:normalize=0", graph)
        self.assertIn("alimiter=", graph)

    def test_mode_ceilings(self):
        self.assertEqual(SHORTS_MODES["classic59"]["max_seconds"], 59)
        self.assertEqual(SHORTS_MODES["extended179"]["max_seconds"], 179)

    def test_custom_effect_stays_in_pack_directory(self):
        with self.assertRaises(ValueError):
            resolve_custom_effect("../outside.mp3")


if __name__ == "__main__":
    unittest.main()
