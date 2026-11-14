"""Offline API contract, US English sidecar and thumbnail smoke tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from core.ai_director import (_payload, _response_text, _validate, APIChoice, direct,
                              discover_api_and_model, local_english_metadata, write_seo_us)
from core.analyzer import Candidate
from core.meme_fetcher import SOUNDS
from core.sfx import director_meme_timeline
from core.thumbnail import generate_thumbnails


class DirectorTests(unittest.TestCase):
    def test_discovery_uses_only_responses_and_falls_back_to_text(self):
        client = MagicMock()
        client.__enter__.return_value = client
        def post(url, **kwargs):
            payload = kwargs["json"]
            if isinstance(payload["input"], list):
                response = requests.Response()
                response.status_code = 400
                raise requests.HTTPError(response=response)
            response = MagicMock()
            response.json.return_value = {"output_text": '{"ok": true}'}
            return response
        client.post.side_effect = post
        with patch("core.ai_director._client", return_value=client):
            choice = discover_api_and_model("https://example.com/api", "dummy")
        self.assertEqual((choice.model, choice.mode), ("gpt-5.6-sol", "telemetry"))
        client.get.assert_not_called()
        self.assertEqual(choice.endpoint, "https://example.com/api/responses")
        self.assertEqual(client.post.call_count, 2)

    def test_responses_transport_and_local_metadata(self):
        payload = _payload("o3-mini", "https://example.com/responses", "test", b"jpeg")
        self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        self.assertEqual(_response_text({"output": [{"content": [{"type": "output_text", "text": "OK"}]}]}), "OK")
        data = local_english_metadata(compilation=True)
        self.assertEqual(len(SOUNDS), 40)
        self.assertEqual(len(data["selected_memes"]), 9)
        self.assertEqual(len(data["us_youtube_description"].splitlines()), 3)
        cues = director_meme_timeline(data["selected_memes"], 13.5, 19)
        self.assertIn(("vine_boom", 13.5, 0.85), cues)

    def test_exact_text_request_and_copy_ready_seo(self):
        client = MagicMock()
        client.__enter__.return_value = client
        response = MagicMock()
        response.json.return_value = {"output": [{"content": [{"type": "output_text", "text": "OK"}]}]}
        client.post.return_value = response
        with patch("core.ai_director._client", return_value=client):
            choice = discover_api_and_model("https://example.com/api", "dummy")
        self.assertEqual(choice.mode, "vision")
        self.assertEqual(set(client.post.call_args.kwargs["json"]), {"model", "input"})
        with tempfile.TemporaryDirectory() as folder:
            output = write_seo_us(Path(folder) / "seo.txt", local_english_metadata())
            contents = output.read_text(encoding="utf-8")
            self.assertIn("TITLE:", contents)
            self.assertIn("DESCRIPTION:", contents)
            self.assertIn("TAGS:", contents)

    def test_bad_meme_rejected(self):
        data = local_english_metadata()
        data["selected_memes"]["impact"] = "not_a_real_file.wav"
        with self.assertRaisesRegex(ValueError, "unknown meme"):
            _validate(data)

    def test_saw_crash_rejects_unseen_train_then_uses_text(self):
        wrong = local_english_metadata()
        wrong["us_youtube_title"] = "Train Crash! 🚂 #shorts"
        correct = local_english_metadata()
        correct["thumbnail_text"] = "LAVA SAW CRASH"
        client = MagicMock()
        client.__enter__.return_value = client
        response1, response2 = MagicMock(), MagicMock()
        response1.json.return_value = {"output_text": json.dumps(wrong)}
        response2.json.return_value = {"output_text": json.dumps(correct)}
        client.post.side_effect = [response1, response2]
        choice = APIChoice("https://example.com/responses", "gpt-5.6-sol", "vision")
        with (patch("core.ai_director._client", return_value=client),
              patch("core.ai_director.sample_frame", return_value=b"jpeg")):
            result, used = direct(Path("-brcT3E5Dcg.mp4"),
                                  [Candidate(146.9, 18, 160.4, 1)],
                                  title="obstacle-course game", choice=choice)
        self.assertEqual(used.mode, "telemetry")
        self.assertEqual(result["thumbnail_text"], "LAVA SAW CRASH")
        self.assertEqual(client.post.call_count, 2)

    def test_demo_thumbnail_dimensions(self):
        source = Path(__file__).resolve().parents[1] / "downloads" / "-brcT3E5Dcg.mp4"
        if not source.exists():
            self.skipTest("Demo video unavailable")
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            vertical, horizontal = generate_thumbnails(source, Candidate(146.9, 18, 160.4, 1),
                                                        Path(folder) / "thumbnail", "TOTAL WRECK!")
            with Image.open(vertical) as image:
                self.assertEqual(image.size, (1080, 1920))
            with Image.open(horizontal) as image:
                self.assertEqual(image.size, (1280, 720))


if __name__ == "__main__":
    unittest.main()
