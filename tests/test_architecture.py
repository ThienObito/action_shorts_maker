"""Studio organization, camera interpolation, event cues and MCP tool surface."""
import json
import tempfile
import unittest
import time
from threading import Event
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

from core.meme_fetcher import SOUNDS, available_memes
from core.project_organizer import sanitize_title, list_completed_projects
from core.renderer import continuous_crop_expression, _render_native
from core.sfx import physics_meme_timeline
from core.pipeline import process_youtube_links


class ArchitectureTests(unittest.TestCase):
    def test_sanitizer_windows_edge_cases(self):
        self.assertEqual(sanitize_title('CON'), '_CON')
        safe = sanitize_title('Big / Small: Car? * | <> " \\ .')
        self.assertFalse(any(c in safe for c in '<>:"/\\|?*'))
        self.assertLessEqual(len(safe), 65)
        self.assertFalse(safe.endswith(('.', ' ')))

    def test_continuous_camera_expression(self):
        centers = np.column_stack((np.linspace(300, 550, 120), np.full(120, 180)))
        expression = continuous_crop_expression(centers, 60, 0, 200, 800)
        self.assertIn('min(t-', expression)
        self.assertNotIn('sendcmd', expression)
        self.assertNotIn('commands.txt', expression)

    def test_renderer_graph_cfr_threads_and_progress(self):
        blank = (0, 0, np.zeros((1,1,3), np.float32), np.ones((1,1,1), np.float32))
        centers = np.column_stack((np.linspace(300, 550, 120), np.full(120, 180)))
        with patch('core.renderer.subprocess.run') as ffmpeg, patch('core.renderer.probe', return_value={'duration': 2}):
            _render_native(Path('source.mp4'), Path('output.mp4'), 0, 2, 1,
                           centers, 800, 450, 1080, 1920, 60,
                           {'zoom': 1.1, 'shake': 4}, blank, blank, False,
                           ['-c:v', 'h264_nvenc'], clean=True, enable_sfx=False)
        cmd = ffmpeg.call_args.args[0]
        graph = cmd[cmd.index('-filter_complex')+1]
        self.assertNotIn('sendcmd', graph)
        self.assertIn('min(t-', graph)
        self.assertIn("overlay=x='-w+w*t/", graph)
        self.assertEqual(cmd[cmd.index('-fps_mode')+1], 'cfr')
        self.assertEqual(cmd[cmd.index('-threads')+1], '6')

    def test_real_pack_and_event_anchors(self):
        self.assertEqual(len(SOUNDS), 40)
        self.assertEqual(len(available_memes()), 40)
        events = {'launch': 1.25, 'bump': 3.15, 'jump': 8.2, 'panic': 12.4,
                  'impact': 13.5, 'secondary': 14.05, 'aftermath': 15.1}
        cues = physics_meme_timeline(events, 18)
        self.assertIn(('vine_boom', 13.5, .38), cues)
        self.assertTrue(any(at == 14.05 for _, at, _ in cues))

    def test_process_tool_and_inventory(self):
        with patch('core.pipeline.process_one', side_effect=lambda url, mode, count, progress: {'url': url, 'mode': mode}) as run:
            result = process_youtube_links(['https://youtu.be/one', 'https://youtu.be/two'])
        self.assertEqual(len(result), 2)
        self.assertEqual(run.call_count, 2)
        with tempfile.TemporaryDirectory() as temp, patch('core.project_organizer.OUTPUTS', Path(temp)):
            project = Path(temp) / 'Example_Title'
            project.mkdir()
            (project / 'project_info.json').write_text('{}', encoding='utf-8')
            self.assertEqual(list_completed_projects()[0]['title_folder'], 'Example_Title')

    def test_mcp_tools_are_registered(self):
        from mcp_server import server, process_youtube_links as mcp_process, list_completed_projects as mcp_list
        self.assertEqual(server.name, 'action_shorts_maker')
        config = json.loads((Path(__file__).resolve().parents[1] / 'opencode.json').read_text())
        self.assertEqual(config['mcp']['servers']['action_shorts_maker']['type'], 'local')
        with patch('mcp_server._process', return_value=[{'folder': 'demo'}]) as process:
            self.assertEqual(mcp_process('https://youtu.be/demo', background=False), [{'folder': 'demo'}])
            self.assertEqual(process.call_args.args, (['https://youtu.be/demo'], 'short', 1))
        with patch('mcp_server._list', return_value=[{'title_folder': 'demo'}]):
            self.assertEqual(mcp_list()[0]['title_folder'], 'demo')

    def test_background_returns_job_and_records_completion(self):
        from mcp_server import process_youtube_links as mcp_process, get_job_status
        completed = Event()
        def fake_process(urls, mode, max_clips, progress):
            progress(35, 'Analyzing crash peaks...')
            completed.set()
            return [{'folder': 'demo'}]
        with tempfile.TemporaryDirectory() as folder:
            with patch('mcp_server.JOBS', Path(folder) / 'jobs'), \
                 patch('mcp_server.LOG', Path(folder) / 'live_status.log'), \
                 patch('mcp_server._process', side_effect=fake_process):
                job = mcp_process('https://youtu.be/demo', mode='both', background=True)
                self.assertIn('job_id', job)
                self.assertTrue(completed.wait(3))
                for _ in range(50):
                    status = get_job_status(job['job_id'])
                    if status['jobs'][0]['status'] == 'completed':
                        break
                    time.sleep(.01)
                self.assertEqual(status['jobs'][0]['status'], 'completed')
                self.assertIn('[100%]', '\n'.join(status['recent_log']))


if __name__ == '__main__':
    unittest.main()
