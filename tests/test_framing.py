"""Wide crop, balanced tracking and framing preset regression checks."""
import unittest
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from config import DEFAULT_FOV_SCALE, FRAMING_MODES, HEIGHT, PRESETS, WIDTH
from core.renderer import _render_native
from core.tracker import AutoCropper, foreground_height, multi_object_center, source_crop_width


class FramingTests(unittest.TestCase):
    def test_full_height_source_width(self):
        self.assertEqual(DEFAULT_FOV_SCALE, 1.75)
        self.assertEqual(source_crop_width(1920, 1080, WIDTH, HEIGHT, 1.0), 608)
        self.assertEqual(source_crop_width(1920, 1080, WIDTH, HEIGHT, 1.75), 1064)
        self.assertEqual(source_crop_width(1920, 1080, WIDTH, HEIGHT, 2.2), 1338)
        self.assertEqual(source_crop_width(1920, 1080, WIDTH, HEIGHT, 2.0), 1216)
        self.assertEqual(foreground_height(WIDTH, 1080, 1064), 1096)
        self.assertEqual(source_crop_width(900, 1080, WIDTH, HEIGHT, 2.5), 900)
        for scale in (1.0, 1.45, 1.75, 2.0, 2.25, 2.5):
            crop_w = source_crop_width(1920, 1080, WIDTH, HEIGHT, scale)
            fg_h = foreground_height(WIDTH, 1080, crop_w)
            self.assertEqual((crop_w % 2, fg_h % 2), (0, 0))
            self.assertLess(abs(crop_w / 1080 - WIDTH / fg_h), 0.01)
            zoom_w = source_crop_width(1920, 1080, WIDTH, HEIGHT, scale, 1.04)
            zoom_h = 2 * round(1080 / 1.04 / 2)
            self.assertLess(abs(zoom_w / zoom_h - WIDTH / foreground_height(WIDTH, zoom_h, zoom_w)), 0.01)

    def test_presets_and_impact_zoom(self):
        self.assertEqual([mode["fov_scale"] for mode in FRAMING_MODES.values()],
                         [1.75, 1.45, 2.25, 1.0])
        self.assertEqual(PRESETS["action"]["zoom"], 1.04)

    def test_vehicle_and_train_union_influences_camera(self):
        boxes = np.array([[60, 150, 140, 230], [470, 100, 590, 260]], dtype=float)
        classes = np.array([2, 6])
        center = multi_object_center(boxes, classes, np.array([100, 190]))
        self.assertGreater(center[0], 240)
        self.assertLess(center[0], 330)
        # Even a dominant train must retain the closest car in the framing union.
        train_dominant = np.array([[10, 170, 80, 225], [470, 35, 620, 290]], dtype=float)
        center = multi_object_center(train_dominant, classes, np.array([540, 160]))
        self.assertLess(center[0], 480)

    def test_foreground_keeps_vertical_canvas_size(self):
        cropper = AutoCropper.__new__(AutoCropper)
        cropper.output_width, cropper.output_height, cropper.fov_scale = 180, 320, 1.75
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        frame[:, :, 1] = 180
        result = cropper.crop(frame, np.array([160, 90]))
        self.assertEqual(result.shape, (320, 180, 3))
        self.assertEqual(int(result[160, 90, 1]), 180)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_rendered_video_has_square_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "source.mp4", Path(folder) / "out.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=320x180:r=30", "-t", "1", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(source)], check=True)
            blank = (0, 0, np.zeros((1, 1, 3), np.float32), np.ones((1, 1, 1), np.float32))
            _render_native(source, output, 0, 0.5, 0.3,
                           np.tile([160, 90], (15, 1)), 320, 180, 180, 320, 30,
                           {"zoom": 1.04, "shake": 0, "caption": ""}, blank, blank,
                           False, ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"],
                           clean=True, enable_sfx=False, fov_scale=1.75)
            result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                     "-show_entries", "stream=width,height,sample_aspect_ratio",
                                     "-of", "json", str(output)], capture_output=True,
                                    text=True, check=True)
            video = json.loads(result.stdout)["streams"][0]
            self.assertEqual((video["width"], video["height"]), (180, 320))
            self.assertEqual(video["sample_aspect_ratio"], "1:1")


if __name__ == "__main__":
    unittest.main()
