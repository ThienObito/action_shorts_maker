"""Boundary and scene-cut regression tests for crash-centered clips."""
import unittest

from core.analyzer import segment_window


class SegmentWindowTests(unittest.TestCase):
    def test_default_includes_run_up_and_aftermath(self):
        self.assertEqual(segment_window(100, 480, []), (86.5, 104.5))

    def test_uses_approach_scene_cut(self):
        self.assertEqual(segment_window(100, 480, [60, 86, 95]), (86, 104.5))

    def test_expands_at_video_start(self):
        self.assertEqual(segment_window(3, 480, []), (0.0, 16.0))

    def test_expands_at_video_end(self):
        self.assertEqual(segment_window(477, 480, []), (463.5, 480))

    def test_short_source_uses_available_duration(self):
        self.assertEqual(segment_window(1, 10, []), (0.0, 10))


if __name__ == "__main__":
    unittest.main()
