"""Offline Studio API guardrails and FFmpeg mask contract."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import studio_server
from core.docu_postprocess import apply_frosted_blur_text_mask
from core.studio_transitions import STYLES, transition_graph
from core.audio_separator import verify_and_clean_machine_sfx
from studio_backfill import _duration, _render_aligned_demucs_sfx, _select_source_audio, ensure_project_stems_complete


class StudioTests(unittest.TestCase):
    def test_masks_have_separate_branches(self):
        for size in ((1920, 1080), (1080, 1920)):
            for mode in ('none', 'top_blur_brand', 'bottom_blur_sub', 'both_top_bottom_blur'):
                graph, label = apply_frosted_blur_text_mask(*size, mode)
                self.assertEqual(bool(graph), mode != 'none')
                if mode != 'none':
                    self.assertIn('[base]', graph)
                    self.assertTrue(label.endswith('out') or label == 'masked')

    def test_project_paths_reject_traversal(self):
        for name in ('../secrets', 'jobs', '.', 'foo/bar', 'foo\\bar', '_archived_empty'):
            with self.assertRaises(ValueError):
                studio_server.project_path(name)

    def test_render_requires_stems(self):
        original = studio_server.OUTPUTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                studio_server.OUTPUTS = Path(directory)
                (studio_server.OUTPUTS / 'IronAxisLab_fixture').mkdir()
                with self.assertRaisesRegex(ValueError, 'Missing individual'):
                    studio_server.validate_render({'project': 'IronAxisLab_fixture', 'sliders': studio_server.SLIDERS,
                                                   'text_blur_mode': 'none'})
        finally:
            studio_server.OUTPUTS = original

    def test_empty_project_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            report = ensure_project_stems_complete(Path(directory))
            self.assertFalse(report['complete'])
            self.assertEqual(set(report['missing']), {'voice', 'sfx', 'visual', 'bgm', 'ass', 'mix'})

    def test_recreated_sfx_covers_master_and_preserves_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'other.wav'
            dest = Path(directory) / 'ambient.wav'
            subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi',
                            '-i', 'sine=frequency=440:duration=1', str(source)], check=True)
            _render_aligned_demucs_sfx(source, dest, 1.5, .5)
            self.assertAlmostEqual(_duration(dest), 1.5, delta=.02)

    def test_vocal_activity_filter_reports_processing_not_speech_certification(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as directory:
            sr = 44100
            t = np.arange(sr * 2) / sr
            vocal = np.zeros_like(t)
            vocal[sr:sr * 2] = .25 * np.sin(2*np.pi*500*t[sr:sr * 2])
            other = .12*np.sin(2*np.pi*500*t) + .08*np.sin(2*np.pi*5000*t)
            vocals, sfx, cleaned = (Path(directory) / name for name in ('vocals.wav', 'other.wav', 'clean.wav'))
            sf.write(vocals, vocal, sr)
            sf.write(sfx, other, sr)
            stats = verify_and_clean_machine_sfx(vocals, sfx, cleaned)
            self.assertGreater(stats['vocal_activity_windows'], 0)
            self.assertLess(stats['vocal_band_attenuation_db'], -5)
            self.assertGreater(stats['machine_band_change_db'], -2)
            self.assertFalse(stats['machine_sfx_verified'])
            self.assertFalse(stats['speech_absence_verified'])
            self.assertAlmostEqual(_duration(cleaned), 2, delta=.01)

    def test_multi_source_backfill_uses_both_audio_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for index, hz in ((1, 300), (2, 600)):
                subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                                f'color=c=black:s=160x90:d=1:r=10', '-f', 'lavfi', '-i',
                                f'sine=frequency={hz}:duration=1', '-shortest',
                                str(folder / f'source_{index:02}.mp4')], check=True)
            subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=black:s=160x90:d=2:r=10', '-an',
                            str(folder / 'source_concat.mp4')], check=True)
            source, description = _select_source_audio(folder)
            self.assertEqual(source.name, 'source_ambient_concat.wav')
            self.assertIn('source_02.mp4', description)
            self.assertAlmostEqual(_duration(source), 2, delta=.1)

    def test_transition_styles_are_bounded_to_real_chapters(self):
        for style in STYLES:
            graph, label = transition_graph(style, [0, 1, 4, 9], 5, 320, 180)
            self.assertEqual(label, 'transitions')
            if style != 'none':
                self.assertTrue('between(t,' in graph or 'between(on,' in graph)
                self.assertNotIn('9.000', graph)

    def test_inventory_does_not_expose_incomplete_render(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            renders = folder / 'studio_renders'
            renders.mkdir()
            complete = renders / 'studio_complete.mp4'
            partial = renders / 'studio_partial.mp4'
            complete.write_bytes(b'complete')
            partial.write_bytes(b'incomplete but nonzero')
            (folder / 'studio_qa.json').write_text(json.dumps({
                'studio_renders/studio_complete.mp4': {'lufs': -14, 'true_peak_dbtp': -2,
                                                       'passed': True}}), encoding='utf-8')
            with patch.object(studio_server, 'probe', return_value={}):
                item = studio_server.inventory(folder)
            self.assertEqual([v['relative'] for v in item['videos']],
                             ['studio_renders/studio_complete.mp4'])
            self.assertEqual(item['agents']['QA']['state'], 'available')

    def test_status_deduplicates_persisted_and_live_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            original = studio_server.OUTPUTS
            try:
                studio_server.OUTPUTS = Path(directory)
                (studio_server.OUTPUTS / 'jobs').mkdir()
                (studio_server.OUTPUTS / 'jobs' / 'studio_same.json').write_text(
                    '{"job_id":"same","status":"queued"}', encoding='utf-8')
                with patch.dict(studio_server.JOBS, {'same': {'job_id': 'same', 'status': 'completed'}}, clear=True):
                    self.assertEqual(studio_server.status()['jobs'], [{'job_id': 'same', 'status': 'completed'}])
            finally:
                studio_server.OUTPUTS = original


if __name__ == '__main__':
    unittest.main()
