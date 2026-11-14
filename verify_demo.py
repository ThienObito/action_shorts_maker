"""Check organized demo and strict 60 fps packet timestamps; refresh expert report."""
import json
import subprocess
from fractions import Fraction
from pathlib import Path

from PIL import Image

from core.pipeline import _video_details, expert_report

folder = Path('outputs/Big_Small_McQueen_Police_VS_Train_BeamNG.drive')
info_path = folder / 'project_info.json'
info = json.loads(info_path.read_text(encoding='utf-8'))
report = (folder / 'expert_director_report.md').read_text(encoding='utf-8')
if len(report) < 300 or '{"error"' in report:
    report, mode = expert_report(info['title'], info['timestamps'], folder / 'storyboard_01.jpg')
    (folder / 'expert_director_report.md').write_text(report, encoding='utf-8')
else:
    mode = 'live-ai'
info['expert_mode'] = mode
for filename, expected_frames in [('short_01_viral_18s.mp4', 1080),
                                  ('classic59_compilation_57s.mp4', 3420)]:
    video = folder / filename
    details = _video_details(video)
    assert details['frames'] == expected_frames
    assert details['fps'] == '60/1'
    assert details['width'] == 1080 and details['height'] == 1920
    response = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                               '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'csv=p=0', str(video)],
                              capture_output=True, text=True, check=True)
    pts = [float(line) for line in response.stdout.splitlines() if line.strip()]
    assert len(pts) == expected_frames
    assert all(abs((pts[i+1]-pts[i])-1/60) < .0001 for i in range(len(pts)-1))
    info['videos'][filename] = details
    print(filename, 'CFR', len(pts), 'frames', details['measured_integrated_lufs'], 'LUFS', flush=True)
for name, size in [('thumb_vertical_1080x1920.jpg', (1080, 1920)),
                   ('thumb_horizontal_1280x720.jpg', (1280, 720))]:
    with Image.open(folder / name) as picture:
        assert picture.size == size
for filename in ('seo_us_copy_paste.txt', 'project_info.json', 'expert_director_report.md'):
    assert (folder / filename).is_file()
info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print('EXPERT_MODE', mode, 'REPORT_CHARS', len(report), 'ASSETS', len(list(folder.iterdir())), flush=True)
