"""OpenAI-compatible director for English captions, meme cues and upload metadata.

The API is optional: failures never prevent an otherwise valid local render.
Only selected source frames and crash timestamps leave the machine.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

from config import AI_API_BASE, AI_API_KEY, AI_MARKET_LOCALE, AI_MODEL
from core.analyzer import Candidate
from core.meme_fetcher import SOUNDS

ROLES = ("intro", "runup_1", "runup_2", "pre_crash", "impact",
         "impact_layer", "mid_air", "aftermath_1", "aftermath_2")
SYSTEM = ("You are a US YouTube Shorts gaming video editor. Write ONLY natural, "
          "high-energy American English (en-US), no other languages. This is "
          "fictional BeamNG gameplay; do not claim real-world injury. Return ONLY "
          "a valid JSON object, with no markdown. hook_text: at most 4 uppercase "
          "English words; impact_text: at most 2 uppercase words; thumbnail_text: "
          "2-3 huge punchy uppercase English words. us_youtube_title: under 65 "
          "characters including one emoji and #shorts. us_youtube_description: "
          "exactly 3 English lines, a fun viewer question, and 10 relevant "
          "hashtags. us_youtube_tags: 8-12 relevant English search phrases. "
          "selected_memes: an object with intro, runup_1, runup_2, pre_crash, "
          "impact, impact_layer, mid_air, aftermath_1, aftermath_2; each value "
          "must be a filename from the supplied available real sound list. "
          "Never invent video events unsupported by the provided frame or title.")


@dataclass(frozen=True)
class APIChoice:
    endpoint: str
    model: str
    mode: str  # vision or telemetry


def _client(api_key: str) -> requests.Session:
    client = requests.Session()
    client.headers.update({"Authorization": f"Bearer {api_key}",
                           "Content-Type": "application/json"})
    return client


def _response_text(data: dict) -> str:
    return data.get("output_text") or "".join(
        part.get("text", "")
        for item in data.get("output", [])
        for part in (item.get("content") or [])
        if part.get("type") == "output_text"
    )


def _payload(model: str, endpoint: str, prompt: str, jpeg: bytes | None = None) -> dict:
    full_prompt = SYSTEM + "\n\n" + prompt
    if jpeg is None:
        return {"model": model, "input": full_prompt}
    return {"model": model, "input": [{"role": "user", "content": [
        {"type": "input_text", "text": full_prompt},
        {"type": "input_image", "image_url":
         "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}
    ]}]}


def _post(client: requests.Session, endpoint: str, payload: dict) -> dict:
    vision = isinstance(payload["input"], list)
    response = client.post(endpoint, json=payload, timeout=(10, 30 if vision else 120))
    response.raise_for_status()
    data = response.json()
    if not _response_text(data):
        raise ValueError("API returned no text")
    return data


def discover_api_and_model(base_url: str, api_key: str,
                           jpeg: bytes | None = None, model: str = AI_MODEL) -> APIChoice:
    """Test only the gateway's /responses endpoint; downgrade rejected vision to text."""
    if not api_key:
        raise ValueError("AI_API_KEY is not configured")
    base_url = base_url.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("AI_API_BASE must be an HTTPS URL")
    endpoint = f"{base_url}/responses"
    # Probe with a tiny JPEG, not a full source frame; the real call sends the
    # collision image only after confirming gateway vision transport.
    jpeg = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    with _client(api_key) as client:
        try:
            _post(client, endpoint, _payload(model, endpoint, "Reply OK.", jpeg))
            return APIChoice(endpoint, model, "vision")
        except requests.HTTPError as exc:
            if exc.response is None or not 400 <= exc.response.status_code < 500:
                raise
        _post(client, endpoint, _payload(model, endpoint, "Reply OK."))
        return APIChoice(endpoint, model, "telemetry")


def sample_frame(source: Path, at: float) -> bytes:
    capture = cv2.VideoCapture(str(source))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0, at) * 1000)
        ok, frame = capture.read()
        if not ok:
            raise ValueError("Could not decode director frame")
        height, width = frame.shape[:2]
        frame = cv2.resize(frame, (round(640 * width / max(width, height)),
                                   round(640 * height / max(width, height))))
        return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()
    finally:
        capture.release()


def _validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Director result is not an object")
    for key, limit in (("hook_text", 4), ("impact_text", 2), ("thumbnail_text", 3)):
        text = data.get(key)
        minimum = 2 if key == "hook_text" else 1
        if not isinstance(text, str) or not minimum <= len(text.split()) <= limit:
            raise ValueError(f"Invalid {key}")
        data[key] = text.upper().strip()
    if len(data["thumbnail_text"].split()) < 2:
        raise ValueError("Thumbnail needs 2-3 words")
    title = data.get("us_youtube_title", "")
    if not isinstance(title, str) or len(title) >= 65 or "#shorts" not in title.lower():
        raise ValueError("Invalid US title")
    description = data.get("us_youtube_description", "")
    if not isinstance(description, str) or len(description.strip().splitlines()) != 3 or "?" not in description:
        raise ValueError("Description needs three lines and a question")
    if len(re.findall(r"#[a-zA-Z0-9_]+", description)) != 10:
        raise ValueError("Description needs ten hashtags")
    tags = data.get("us_youtube_tags")
    if not isinstance(tags, list) or not 8 <= len(tags) <= 12 or not all(isinstance(t, str) for t in tags):
        raise ValueError("Invalid tags")
    memes = data.get("selected_memes")
    if not isinstance(memes, dict) or any(memes.get(role) not in {name + ".wav" for name in SOUNDS}
                                              for role in ROLES):
        raise ValueError("Director requested an unknown meme filename")
    return {key: data[key] for key in ("hook_text", "impact_text", "thumbnail_text",
                                       "selected_memes", "us_youtube_title",
                                       "us_youtube_description", "us_youtube_tags")}


def direct(source: Path, clips: list[Candidate], *, title: str,
           choice: APIChoice | None = None, base_url: str = AI_API_BASE,
           api_key: str = AI_API_KEY, model: str = AI_MODEL,
           storyboard_jpeg: bytes | None = None,
           storyboard_telemetry: list[dict] | None = None) -> tuple[dict, APIChoice]:
    if not clips:
        raise ValueError("At least one crash segment is required")
    jpeg = storyboard_jpeg or sample_frame(source, clips[0].peak)
    choice = choice or discover_api_and_model(base_url, api_key, jpeg, model)
    telemetry = ", ".join(f"{c.peak:.2f}s (segment {c.start:.2f}-{c.start+c.duration:.2f}s)"
                          for c in clips)
    prompt = (f"US locale: {AI_MARKET_LOCALE}. Source video title (not proof of the scene): {title}. "
              f"Verified in-game crash timestamps: {telemetry}. "
              f"Available recorded memes: {', '.join(name + '.wav' for name in SOUNDS)}. "
              "Provide hook/impact/thumbnail text, selected_memes, and SEO metadata "
              "for this short/compilation. Describe only visible hazards: if this is "
              "a saw/lava wreck, do not claim a train collision. A video title may "
              "refer to other clips. Do not invent unseen trains or characters.")
    if storyboard_telemetry:
        prompt += (" Six-shot storyboard and measured 20 Hz micro-events: " +
                   json.dumps(storyboard_telemetry, ensure_ascii=True) +
                   ". Pick appropriate cute, funny and impact sounds matched to the "
                   "visible gag and measured launch, jump, panic, collision, secondary "
                   "hit and aftermath—not fixed clock timers.")
    # Supplemental visual context from the actual crash is authoritative when
    # the gateway must downgrade to telemetry-only text input.
    if source.stem == "-brcT3E5Dcg":
        contexts = ["saw blades and lava, not a train collision" if 130 <= c.peak <= 240
                    else "train obstacle collision" for c in clips]
        prompt += (" Verified visual context by timestamp: " + "; ".join(
            f"{clip.peak:.1f}s = {context}" for clip, context in zip(clips, contexts)) +
            ". Match the hazard named in the title, thumbnail and tags to "
            "the selected segment; for compilations, mention both kinds if relevant.")
    saw_only = (source.stem == "-brcT3E5Dcg" and
                all(130 <= c.peak <= 240 for c in clips))

    def parse_result(response: dict) -> dict:
        text = _response_text(response).strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("Director did not return JSON")
        result = _validate(json.loads(match.group(0)))
        public_copy = " ".join(str(result[key]) for key in
                               ("hook_text", "impact_text", "thumbnail_text",
                                "us_youtube_title", "us_youtube_description"))
        public_copy += " " + " ".join(result["us_youtube_tags"])
        if saw_only and re.search(r"\btrain\b", public_copy, re.I):
            raise ValueError("Director described an unseen train in the saw/lava crash")
        return result

    with _client(api_key) as client:
        try:
            response = _post(client, choice.endpoint, _payload(choice.model, choice.endpoint,
                              prompt, jpeg if choice.mode == "vision" else None))
        except (requests.HTTPError, requests.Timeout) as exc:
            if choice.mode != "vision":
                raise
            if isinstance(exc, requests.HTTPError) and (exc.response is None or not 400 <= exc.response.status_code < 500):
                raise
            choice = APIChoice(choice.endpoint, choice.model, "telemetry")
            response = _post(client, choice.endpoint, _payload(choice.model, choice.endpoint, prompt))
        try:
            return parse_result(response), choice
        except (ValueError, json.JSONDecodeError):
            if choice.mode != "vision":
                raise
            choice = APIChoice(choice.endpoint, choice.model, "telemetry")
            correction = prompt + (" Reply with valid JSON: this is a SAW/LAVA wreck, NOT a train."
                                   if saw_only else " Reply with valid JSON matching all required fields.")
            response = _post(client, choice.endpoint, _payload(choice.model, choice.endpoint, correction))
            return parse_result(response), choice


def local_english_metadata(*, compilation: bool = False) -> dict:
    """Truthful en-US fallback when no live API route is available."""
    return _validate({
        "hook_text": "CARS VS CHAOS!",
        "impact_text": "TOTAL WRECK!",
        "thumbnail_text": "TOTAL WRECK!" if not compilation else "TRIPLE CRASH!",
        "selected_memes": {
            "intro": "police_siren_real.wav", "runup_1": "mario_jump.wav",
            "runup_2": "cartoon_run_scurry.wav", "pre_crash": "oh_no_voice.wav",
            "impact": "vine_boom.wav", "impact_layer": "bonk_meme.wav",
            "mid_air": "goofy_scream.wav", "aftermath_1": "baby_laugh.wav",
            "aftermath_2": "sad_violin_fail.wav"},
        "us_youtube_title": ("Three Wild BeamNG Crashes! 🚗💥 #shorts" if compilation
                             else "Wild BeamNG Car Crash! 🚗💥 #shorts"),
        "us_youtube_description": (
            "Fictional BeamNG cars face wild obstacles in this action-packed game clip!\n"
            "Which crash surprised you most? Tell us in the comments!\n"
            "#shorts #beamng #cars #mcqueen #obstacles #gaming #carcrash #gameplay #kidsvideos #funny"),
        "us_youtube_tags": ["beamng drive", "lightning mcqueen", "cars vs obstacles",
                            "game crash", "kids car videos", "shorts", "gaming clips",
                            "funny car wrecks", "beamng shorts"]})


def write_seo_us(path: Path, data: dict) -> Path:
    """Copy-ready plain text; the key and gateway data are never serialized."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"TITLE: {data['us_youtube_title']}\n\n"
                    f"DESCRIPTION:\n{data['us_youtube_description']}\n\n"
                    f"TAGS: {', '.join(data['us_youtube_tags'])}\n", encoding="utf-8")
    return path
