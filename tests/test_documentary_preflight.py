"""Fast contract checks for documentary preflight and caption/audio routing."""
from __future__ import annotations

import unittest
from pathlib import Path

import pysrt

from core.audio_separator import _atempo
from core.bgm_composer import TRACKS, compose_dynamic_bgm
from long_docu_pipeline import FINAL_NVENC, VISUAL_NVENC, SHARP_FILTER, _master_srt_to_ass, _shorts_srt_to_ass


class DocumentaryPreflightTests(unittest.TestCase):
    def test_master_ass_geometry_and_word_count(self):
        cues = pysrt.from_string("1\n00:00:00,000 --> 00:00:03,000\nOne two three four five six seven eight nine ten eleven.\n")
        ass = _master_srt_to_ass(cues)
        self.assertIn("PlayResX: 1920", ass)
        self.assertIn("PlayResY: 1080", ass)
        self.assertIn("Arial,44", ass)
        self.assertIn(",55,55,42,1", ass)
        self.assertEqual(ass.count("Dialogue:"), 2)

    def test_shorts_ass_geometry(self):
        cues = pysrt.from_string("1\n00:00:00,000 --> 00:00:01,000\nMachine sounds under the English narration.\n")
        ass = _shorts_srt_to_ass(cues)
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("PlayResY: 1920", ass)
        self.assertIn("Arial,58", ass)
        self.assertIn(",60,60,290,1", ass)

    def test_video_and_audio_modes(self):
        self.assertEqual(len(TRACKS), 6)
        self.assertIn("cas=strength=0.55", SHARP_FILTER)
        self.assertIn("-cq", FINAL_NVENC)
        self.assertIn("17", FINAL_NVENC)
        self.assertIn("constqp", VISUAL_NVENC)
        self.assertIn("atempo=2", _atempo(4.8))
        silent = compose_dynamic_bgm(Path("."), 1000, [], bgm_style="none")
        self.assertEqual(len(silent), 1000)


if __name__ == "__main__":
    unittest.main()
