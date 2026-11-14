"""Create an original, source-grounded US English documentary narration."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests
import base64
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from config import AI_API_BASE, AI_API_KEY, AI_MODEL

NICHES = {"engineering_machining", "woodworking_bonsai", "survival_commentary"}

FALLBACK_ENGINEERING_PLAN = {
    "us_clickbait_titles": [
        "How an Irregular Steel Workpiece Is Turned on a Manual Lathe",
        "The Clearance Challenge Behind This Heavy Lathe Operation",
        "Turning Clean Journals on a Complex Metal Workpiece",
        "What the Chips Reveal About This Lathe Cutting Process",
        "A Close Look at Support and Tool Clearance in Heavy Machining",
    ],
    "seo_description": "A visual engineering breakdown of a large irregular metal workpiece being turned on a conventional lathe. Follow the single-point cutter through tight clearance as it forms clean cylindrical lands and shoulders, leaving blue and silver swarf across the carriage.",
    "tags": ["manual lathe", "machining", "heavy engineering", "shaft turning", "machine shop", "metalworking", "industrial machining", "turning process"],
    "thumbnail_prompt": "Realistic 16:9 close-up of irregular metal workpiece and single-point cutting tool on lathe, high-contrast workshop light, no invented sparks, gauges, or text.",
    "segments": [
        {"chapter": 1, "start_time": 0.0, "end_time": 79.68, "on_screen_tech_spec": "STAGE 1: IRREGULAR WORKPIECE | MANUAL LATHE", "narration_en": "A massive, irregular workpiece spins between the centers of a conventional engine lathe. Its offset arms sweep past the cutting tool with every revolution, leaving only a narrow window for the cut. Bright bands along the center show where the steel has already been turned. The darker faces still carry their original rough surface. This is no ordinary round bar. The machinist is creating precise cylindrical lands inside a complex shape, while keeping every rotating section clear of the tool and machine. That balance of reach, support, and clearance sets the challenge for the entire operation."},
        {"chapter": 2, "start_time": 79.68, "end_time": 189.24, "on_screen_tech_spec": "STAGE 2: TAILSTOCK SUPPORT | SINGLE-POINT TOOL", "narration_en": "With the spindle stopped, the geometry comes into focus. Several offset arms surround a set of bright cylindrical journals. A pointed tailstock center supports the work at its end, while a single-point cutter sits ready above the chip-covered carriage. The tool must reach the journal without entering the path of those sweeping arms. On a standard shaft, the cut is continuous. Here, the changing outline makes clearance part of every tool move. Blue and silver swarf gathers below the cut, tracing the path from rough surface to clean machined band."},
        {"chapter": 3, "start_time": 189.24, "end_time": 308.76, "on_screen_tech_spec": "STAGE 3: JOURNAL TURNING | RESTRICTED CLEARANCE", "narration_en": "As the lathe turns, the offset arms blur into repeating arcs around the spindle axis. Between them, a central journal takes shape under the cutting edge. This is the key to the operation: the target surface must run true on the axis, while the larger sections rotate past without striking the tool. The cutter advances along the work and leaves fine turning lines on the bright steel. Each pass builds a cleaner cylindrical land. The tight gap between the tool and neighboring arms demands rigid support and deliberate positioning at every stage."},
        {"chapter": 4, "start_time": 308.76, "end_time": 408.36, "on_screen_tech_spec": "STAGE 4: TURNED NECK AND SHOULDER", "narration_en": "A closer view reveals the tailstock center seated in the end of the workpiece. Just inside it, a narrow turned neck meets a broader shoulder. The tool approaches from below as curled chips collect around the carriage. Supporting the far end helps resist bending while the cutter follows this narrow section. The shoulder marks a sharp change in diameter, so tool position and clearance must stay controlled. The lathe's basic motion remains simple: rotate the work, feed the edge, and let each pass define the surface."},
        {"chapter": 5, "start_time": 408.36, "end_time": 498.0, "on_screen_tech_spec": "STAGE 5: FINISHED JOURNAL | TURNING MARKS VISIBLE", "narration_en": "The final close-up brings the finished journal into view. Even turning lines run along its bright surface, ending at a crisp shoulder beneath the darker outer form. The surrounding bed is littered with swarf from the cutting passes. Across the sequence, the machinist has transformed an irregular blank into a series of cylindrical lands, necks, and shoulders. The job depends on keeping the work supported, the tool aligned, and the rotating arms clear. What began as a difficult shape now carries clean, readable geometry, created one controlled pass at a time."},
    ],
}


def rewrite_documentary(source_text: str, niche_mode: str = "engineering_machining",
                        target_duration_sec: int = 600, keyframes: list[Path] | None = None,
                        source_title: str = "",
                        usable_source_duration_sec: float | None = None) -> dict[str, Any]:
    if niche_mode not in NICHES:
        raise ValueError(f"niche_mode must be one of {sorted(NICHES)}")
    if not source_text.strip() and not keyframes:
        raise ValueError("Provide transcript/scene notes or keyframes for visual analysis")
    if not AI_API_KEY:
        raise RuntimeError("Configure CODEX_API_KEY in project .env")
    duration = max(60, int(target_duration_sec))
    if usable_source_duration_sec is not None:
        duration = min(duration, max(60, int(usable_source_duration_sec * 0.96)))
    prompt = f"""Create original, fact-grounded US English engineering documentary narration.
Use a fluent, vivid US engineering-documentary voice. Explain the visible mechanical challenge,
show how each tool move changes the work, and build curiosity across chapters. Do not invent
measurements, operations, materials, or outcomes absent from source. Avoid audit-style disclaimers
and phrases such as "cannot be confirmed", "do not establish", "should not be assumed",
"remains uncertain", or "no transcript". State visible evidence directly.
Niche: {niche_mode}. Runtime: {duration} seconds. Target roughly {round(duration * 140 / 60)} spoken words (135-145 words/minute).
Return ONLY JSON with keys: segments, us_clickbait_titles (5), seo_description,
tags (array), thumbnail_prompt. Divide narration into exactly five chapters. Each chapter narration
must contain roughly its proportional share of the word target; for a 600-second runtime, write
about 260-290 spoken words per chapter (at least 1,250 total). Do not summarize; explain visible
setup, operation, checks, transitions, and result in complete spoken prose.
Each segment: start_time, end_time (seconds), narration_en, on_screen_tech_spec,
video_speed_factor (always 1.0). Cover full runtime with contiguous, increasing times.
Use chapter boundaries at 0%, 16%, 38%, 62%, 82%, 100%. Narration must cover each chapter
continuously at about 145-155 words/minute. At long runtimes, produce enough narration to fill
the timeline; never pad chapters with long silence. Describe visible operations
supported by provided keyframes. Source title: {source_title}. Available notes: {source_text[:50000] or 'Use visual evidence only.'}"""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_path in (keyframes or [])[:10]:
        encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    errors = []
    target_words = duration * 140 / 60
    responses_url = AI_API_BASE.rstrip("/") + "/responses"
    chat_url = AI_API_BASE.rstrip("/") + "/chat/completions"
    attempts = [
        (responses_url, {"model": AI_MODEL, "input": prompt +
            f"\nFINAL VALIDATION: attempt {attempt}/3. Return all five chapters and at least "
            f"{int(target_words * 0.85)} spoken words total. Do not return an outline or sample.",
            "stream": True})
        for attempt in range(1, 4)
    ]
    attempts.append((chat_url, {"model": AI_MODEL,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"}}))
    for endpoint, payload in attempts:
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
            if not response.ok:
                errors.append(f"{endpoint}: HTTP {response.status_code} {response.text[:300]}")
                continue
            text = ""
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                errors.append(f"{endpoint}: server returned HTML instead of API data")
                continue
            if "text/event-stream" in content_type:
                chunks = []
                event_types = set()
                for line in response.text.splitlines():
                    if line.startswith("data:") and line[5:].strip() != "[DONE]":
                        try:
                            event = json.loads(line[5:].strip())
                            event_type = event.get("type", "")
                            event_types.add(event_type or "unknown")
                            if event_type in ("response.output_text.delta", "response.text.delta"):
                                piece = event.get("delta", "")
                                if isinstance(piece, str) and piece:
                                    chunks.append(piece)
                            elif event_type in ("response.output_text.done", "response.text.done"):
                                final_text = event.get("text") or event.get("output_text")
                                if isinstance(final_text, str) and final_text:
                                    chunks = [final_text]
                            elif event_type in ("response.completed", "response.done"):
                                completed = event.get("response", {})
                                final_text = completed.get("output_text", "")
                                if not final_text:
                                    final_text = "".join(
                                        part.get("text", "")
                                        for item in completed.get("output", [])
                                        for part in item.get("content", [])
                                        if isinstance(part, dict) and part.get("type") in ("output_text", "text"))
                                if final_text:
                                    chunks = [final_text]
                            elif "choices" in event and event["choices"]:
                                choice = event["choices"][0]
                                delta = choice.get("delta", {}).get("content") or choice.get("text", "")
                                if isinstance(delta, str) and delta:
                                    chunks.append(delta)
                        except json.JSONDecodeError:
                            continue
                text = "".join(chunks)
                if not text:
                    errors.append(f"{endpoint}: event stream has no output text; event_types={sorted(event_types)}")
                    continue
            if "text/event-stream" not in content_type:
                data = response.json()
                if "choices" in data:
                    message = data["choices"][0]["message"].get("content", "")
                    text = message if isinstance(message, str) else "".join(
                        part.get("text", "") for part in message if isinstance(part, dict))
                else:
                    text = ""
                if not text and data.get("output"):
                    text = "".join(part.get("text", "") for item in data["output"]
                                   for part in item.get("content", []) if part.get("type") == "output_text")
            if not text:
                errors.append(f"{endpoint}: response contains no text")
                continue
            try:
                result = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
                segments = result.get("segments", [])
                actual_words = sum(len(str(s.get("narration_en", "")).split()) for s in segments)
                if (segments and all(str(s.get("narration_en", "")).strip() for s in segments)
                        and actual_words >= target_words * 0.85):
                    return result
                errors.append(f"{endpoint}: narration too short/invalid: {actual_words} words; target {target_words:.0f}")
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                errors.append(f"{endpoint}: invalid JSON/schema: {exc}")
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    raise RuntimeError("Codex API failed: " + " | ".join(errors))


def format_srt(segments: list[dict[str, Any]]) -> str:
    return split_into_short_srt_cues(segments)


def format_srt_from_audio(segments: list[dict[str, Any]], audio_durations: list[float],
                          max_words_per_cue: int = 10, max_chars_per_cue: int = 52) -> str:
    """Time short subtitle cues against each synthesized chapter's measured audio duration."""
    if len(segments) != len(audio_durations):
        raise ValueError("Each narration segment needs one measured audio duration")
    lines = []
    cue_id = 1
    for segment, duration in zip(segments, audio_durations):
        words = re.findall(r"\S+", segment["narration_en"].strip())
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            proposed = " ".join(current + [word])
            if current and (len(current) >= max_words_per_cue or len(proposed) > max_chars_per_cue):
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))
        if not chunks:
            continue
        weights = [max(1, len(chunk)) for chunk in chunks]
        total = sum(weights)
        start = float(segment["start_time"])
        audio_end = min(float(segment["end_time"]), start + float(duration))
        available = max(0.35, audio_end - start)
        cursor = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            end = audio_end if index == len(chunks) - 1 else start + available * sum(weights[:index + 1]) / total
            end = max(cursor + min(0.35, available / len(chunks)), end)
            def stamp(seconds: float) -> str:
                millis = round(seconds * 1000)
                hours, millis = divmod(millis, 3_600_000)
                minutes, millis = divmod(millis, 60_000)
                secs, millis = divmod(millis, 1000)
                return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
            lines.append(f"{cue_id}\n{stamp(cursor)} --> {stamp(end)}\n{chunk}")
            cue_id += 1
            cursor = end
    return "\n\n".join(lines) + "\n"


def split_into_short_srt_cues(segments: list[dict[str, Any]], max_words_per_cue: int = 10,
                              max_chars_per_cue: int = 52) -> str:
    """Split chapter narration into timed, single-line cues; never exceed word/character caps."""
    import re
    def stamp(seconds: float) -> str:
        millis = round(float(seconds) * 1000)
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
    cues = []
    for seg in segments:
        words = re.findall(r"\S+", seg["narration_en"].strip())
        chunks, current = [], []
        for word in words:
            proposal = " ".join(current + [word])
            if current and (len(current) >= max_words_per_cue or len(proposal) > max_chars_per_cue):
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))
        if not chunks:
            continue
        start, end = float(seg["start_time"]), float(seg["end_time"])
        weights = [max(1, len(chunk)) for chunk in chunks]
        total = sum(weights)
        cursor = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            cue_end = end if index == len(chunks) - 1 else cursor + (end - start) * weight / total
            # Round cue boundaries and keep short minimum readable duration without crossing chapter end.
            cue_end = max(cursor + 0.35, min(end, cue_end))
            cues.append((cursor, cue_end, chunk))
            cursor = cue_end
    return "\n\n".join(f"{i}\n{stamp(start)} --> {stamp(end)}\n{text}"
                        for i, (start, end, text) in enumerate(cues, 1)) + "\n"


def save_metadata(result: dict[str, Any], folder: Path) -> None:
    titles = "\n".join(f"{i}. {title}" for i, title in enumerate(result["us_clickbait_titles"], 1))
    (folder / "us_seo_metadata.md").write_text(
        f"# Titles\n{titles}\n\n# Description\n{result['seo_description']}\n\n"
        f"# Tags\n{', '.join(result['tags'])}\n\n# Thumbnail prompt\n{result['thumbnail_prompt']}\n",
        encoding="utf-8")
