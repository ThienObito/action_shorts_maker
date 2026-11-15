"""Render a two-second Studio derivative from synthetic stems (offline, temporary)."""
import subprocess
import sys
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import studio_server


def main():
    with tempfile.TemporaryDirectory(prefix='ironaxis_studio_') as directory:
        folder = Path(directory)
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=1920x1080:rate=30:duration=2', '-an',
                        '-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'constqp', '-qp', '14',
                        str(folder / 'visual_master_silent.mp4')], check=True)
        for name, freq in [('full_voiceover.wav', 330), ('bgm_master.wav', 180), ('ambient_bed.wav', 770)]:
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            f'sine=frequency={freq}:duration=2', '-ar', '48000', '-ac', '2',
                            str(folder / name)], check=True)
        (folder / 'english_narration.ass').write_text(
            '[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n'
            '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
            'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, '
            'Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,Arial,44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1.5,2,55,55,42,1\n'
            '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,Test industrial narration\n', encoding='utf-8')
        (folder / 'english_narration_timeline.json').write_text(
            json.dumps({'segments': [{'chapter': 1, 'start_sec': .1, 'end_sec': .9},
                                     {'chapter': 2, 'start_sec': 1, 'end_sec': 1.9}]}), encoding='utf-8')
        job = 'smoketest'
        studio_server.JOBS[job] = {'job_id': job, 'status': 'queued'}
        studio_server.render_worker(job, folder, dict(studio_server.SLIDERS), 'both_top_bottom_blur',
                                    'cross_dissolve')
        status = studio_server.JOBS[job]
        assert status['status'] == 'completed', status
        assert (folder / status['output']).is_file(), status
        assert abs(float(studio_server.probe(folder / status['output'])['format']['duration']) - 2) < .2
        print('Studio derivative PASS', status['loudness_lufs'], 'LUFS')


if __name__ == '__main__':
    main()
