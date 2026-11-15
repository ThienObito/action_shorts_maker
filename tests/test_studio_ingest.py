"""Local-only intake API and responsive drawer contracts without network downloads."""
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import studio_server
from studio_ingest import import_local_video, validate_source_url


class IntakeTests(unittest.TestCase):
    def test_supported_hosts_and_no_traversal(self):
        for url in ('https://www.facebook.com/reel/123', 'https://fb.watch/abc',
                    'https://youtube.com/shorts/abc', 'https://youtu.be/abc',
                    'https://www.tiktok.com/@a/video/123'):
            self.assertEqual(validate_source_url(url), url)
        for url in ('http://youtube.com/watch?v=abc', 'https://youtube.com.evil.invalid/a',
                    'file:///secret', 'https://u:p@facebook.com/reel/123'):
            with self.assertRaises(ValueError):
                validate_source_url(url)

    def test_import_local_video_isolated_and_probed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=blue:s=320x180:d=1:r=10', str(source)], check=True)
            (root / 'outputs').mkdir()
            result = import_local_video(root / 'outputs', source, '../../my project')
            self.assertTrue(result['project'].startswith('IronAxisLab_my_project_'))
            self.assertEqual(result['metadata']['width'], 320)
            self.assertTrue((root / 'outputs' / result['project'] / 'source_01.mp4').is_file())
            self.assertFalse((root / 'outputs' / result['project'] / 'final_us_documentary_1080p60.mp4').exists())
            with patch.object(studio_server, 'OUTPUTS', root / 'outputs'):
                with self.assertRaisesRegex(ValueError, 'four pre-flight'):
                    studio_server.validate_pipeline({'project': result['project']})
                options = {'project': result['project'], 'source_audio_mode': 'mute_all',
                           'text_blur_mode': 'none', 'bgm_style': 'none',
                           'niche_mode': 'engineering_machining'}
                folder, params = studio_server.validate_pipeline(options)
                self.assertEqual(folder, root / 'outputs' / result['project'])
                self.assertEqual(params['local_source_files'], [folder / 'source_01.mp4'])

    def test_picker_download_and_drawers(self):
        html = Path(studio_server.ROOT / 'studio_ui.html').read_text(encoding='utf-8')
        self.assertGreaterEqual(html.count('class="glass-drawer"'), 7)
        self.assertIn('threeBgCanvas', html)
        self.assertIn('id="dropzone"', html)
        server = ThreadingHTTPServer(('127.0.0.1', 0), studio_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body):
            return json.load(urlopen(Request(f'http://127.0.0.1:{server.server_port}{path}',
                                             data=json.dumps(body).encode(), method='POST',
                                             headers={'Content-Type': 'application/json',
                                                      'Origin': 'http://127.0.0.1:8765'})))
        try:
            with patch('tkinter.Tk') as root, patch('tkinter.filedialog.askopenfilename', return_value=r'C:\video.mp4'), \
                    patch.object(studio_server, 'import_local_video', return_value={'project': 'IronAxisLab_test'}):
                self.assertEqual(request('/api/pick_local_video', {})['project'], 'IronAxisLab_test')
                self.assertTrue(root.called)
            with patch.object(studio_server, 'download_worker') as worker:
                item = request('/api/download_source_url', {'url': 'https://fb.watch/abc'})
                self.assertEqual(item['kind'], 'download')
                self.assertTrue(worker.called or item['status'] == 'queued')
            with self.assertRaises(HTTPError) as response:
                request('/api/download_source_url', {'url': 'https://facebook.com.evil.invalid/a'})
            self.assertEqual(response.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_raw_drag_drop_upload_creates_previewable_project(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs = Path(directory) / 'outputs'
            outputs.mkdir()
            source = Path(directory) / 'example.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=red:s=320x180:d=1:r=10', str(source)], check=True)
            with patch.object(studio_server, 'OUTPUTS', outputs):
                server = ThreadingHTTPServer(('127.0.0.1', 0), studio_server.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    request = Request(f'http://127.0.0.1:{server.server_port}/api/upload_local_video',
                                      data=source.read_bytes(), method='POST', headers={
                                          'Content-Type': 'application/octet-stream',
                                          'Origin': 'http://127.0.0.1:8765',
                                          'X-Filename': 'example.mp4', 'X-Project-Name': 'test'})
                    result = json.load(urlopen(request))
                    self.assertEqual(result['metadata']['width'], 320)
                    item = json.load(urlopen(f'http://127.0.0.1:{server.server_port}/api/project/{result["project"]}'))
                    self.assertEqual(item['videos'][0]['relative'], 'source_01.mp4')
                    self.assertFalse(item['render_ready'])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)
