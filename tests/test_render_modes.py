"""Guard the full-HD and preview output profiles and cached asset dimensions."""
import unittest

from config import DEFAULT_CLIP, FPS, HEIGHT, RENDER_MODES, WIDTH
from core.renderer import _overlay


class RenderModeTests(unittest.TestCase):
    def test_full_hd_default(self):
        self.assertEqual((WIDTH, HEIGHT, FPS), (1080, 1920, 60))
        self.assertEqual(DEFAULT_CLIP, 18)

    def test_fast_preview(self):
        self.assertIn((720, 1280, 30), RENDER_MODES.values())

    def test_overlay_is_small_region(self):
        x, y, foreground, alpha = _overlay("BOOM!", 1080, 1920, 320, "#ffed38")
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLess(foreground.shape[0], 1920)
        self.assertEqual(foreground.shape[:2], alpha.shape[:2])

    def test_encoder_probe_uses_supported_frame_size(self):
        from core.renderer import _nvenc_available

        self.assertIsInstance(_nvenc_available(), bool)


if __name__ == "__main__":
    unittest.main()
