"""Real-meme provenance, audio format, cue alternation, and parser coverage."""
import json
import unittest

import soundfile as sf

from config import DEFAULT_MEME_STYLE, MEME_STYLES
from core.meme_fetcher import MANIFEST, MEME_DIR, SOUNDS, available_memes, search_myinstants
from core.sfx import real_meme_timeline


class RealMemeTests(unittest.TestCase):
    def test_scraper_extracts_labeled_direct_sound(self):
        class Response:
            text = ('<div class="instant"><button class="small-button" '
                    'onclick="play(\'/media/sounds/boom.mp3\', \'loader\', \'id\')"></button>'
                    '<a class="instant-link">Vine Boom</a></div>')

            def raise_for_status(self):
                pass

        class Session:
            def get(self, *args, **kwargs):
                return Response()

        self.assertEqual(search_myinstants("vine boom", Session()),
                         [("Vine Boom", "https://www.myinstants.com/media/sounds/boom.mp3")])

    def test_pack_is_real_and_normalized(self):
        self.assertEqual(len(SOUNDS), 40)
        self.assertEqual(DEFAULT_MEME_STYLE, "real")
        self.assertIn("synthetic", MEME_STYLES)
        self.assertEqual(set(available_memes()), set(SOUNDS))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for name in SOUNDS:
            self.assertEqual(manifest[name]["source"], "myinstants")
            self.assertTrue(manifest[name]["url"].startswith("https://www.myinstants.com/media/sounds/"))
            info = sf.info(MEME_DIR / f"{name}.wav")
            self.assertEqual((info.samplerate, info.channels), (44100, 2))
            self.assertGreaterEqual(info.duration, 0.5)
            self.assertLessEqual(info.duration, 2.21)
            self.assertLessEqual(manifest[name]["peak_dbfs"], -0.9)

    def test_compilation_jokes_vary(self):
        first = real_meme_timeline(13.5, 19, 0)
        second = real_meme_timeline(13.5, 19, 1)
        self.assertEqual(len(first), 10)
        self.assertNotEqual(first[0][0], second[0][0])
        self.assertIn(("vine_boom", 13.5, 1.0), first)
        self.assertIn(("bonk_meme", 13.5, 0.75), first)
        self.assertIn(("airhorn_mlg", 13.5, 0.75), second)

    def test_ultra_density_keeps_impact_locked(self):
        cues = real_meme_timeline(13.5, 19, density="ultra")
        self.assertGreaterEqual(len(cues), 17)
        self.assertIn(("vine_boom", 13.5, 0.8), cues)


if __name__ == "__main__":
    unittest.main()
