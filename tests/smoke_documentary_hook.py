"""Small offline end-to-end hook smoke; writes only into an OS temp directory."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from long_docu_pipeline import assemble_and_master_video


def run():
    with tempfile.TemporaryDirectory(prefix="docu_hook_") as directory:
        folder = Path(directory)
        source = folder / "fixture.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x720:rate=30:duration=11", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=11", "-c:v", "h264_nvenc", "-preset", "p5",
                        "-c:a", "aac", "-shortest", str(source)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=660:duration=18", "-ar", "48000", "-ac", "2",
                        str(folder / "master_audio_mix.wav")], check=True)
        (folder / "english_narration.srt").write_text(
            "1\n00:00:08,000 --> 00:00:13,000\nSHARP CAPTION FOR MASTER CHECK\n", encoding="utf-8")
        (folder / "body.srt").write_text(
            "1\n00:00:00,000 --> 00:00:05,000\nSHARP CAPTION FOR MASTER CHECK\n", encoding="utf-8")
        output = assemble_and_master_video([source], {"title": "HOOK PROBE", "segments": [
            {"on_screen_tech_spec": "HUD PROBE", "hud_start": 8, "hud_end": 13}]},
            folder / "master_audio_mix.wav", folder, make_intro_hook=True,
            speed_up_source=True, subtitle_path=folder / "body.srt")
        def probe(path):
            return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams",
                                              "-show_format", "-of", "json", str(path)],
                                             capture_output=True, text=True, check=True).stdout)
        master = probe(output)
        silent = probe(folder / "visual_master_silent.mp4")
        assert not any(s["codec_type"] == "audio" for s in silent["streams"])
        assert abs(float(master["format"]["duration"]) - 18) < .5
        assert abs(float(silent["format"]["duration"]) - 18) < .5
        assert next(s for s in master["streams"] if s["codec_type"] == "video")["avg_frame_rate"] == "60/1"
        assert next(s for s in silent["streams"] if s["codec_type"] == "video")["avg_frame_rate"] == "60/1"
        assert (folder / "master_subtitles.ass").is_file()
        # Compare single frames from the two video tracks after the hook; no overlays on Shorts source.
        def frame(path, second):
            return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(second), "-i", str(path),
                                   "-frames:v", "1", "-vf", "crop=600:240:650:800", "-f", "rawvideo",
                                   "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
        assert frame(output, 9) != frame(folder / "visual_master_silent.mp4", 9)
        assert frame(output, 12) != frame(folder / "visual_master_silent.mp4", 12)
        print("Hook smoke PASS: 18s / 60fps master with text/HUD; silent 60fps clean visual; NVENC render")


if __name__ == "__main__":
    run()
