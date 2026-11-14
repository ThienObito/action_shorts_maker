"""Sequential, restartable fresh documentary batch for IronAxis Lab."""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from core.docu_postprocess import self_verify_output
from long_docu_pipeline import produce_documentary

OUTPUTS = Path(__file__).resolve().parent / "outputs"
PROJECTS = [
    ("01_clamp", ["6LunIo8Z-fE"], []),
    ("02_shaft_two_parts", ["OT_68o7ysdo", "_RQqso-ulkE"], [(350, 357)]),
    ("03_angle_grinder_jig", ["0D8NbTa77os"], []),
    ("04_eccentric_shaft_two_parts", ["qOmXWUeBEDA", "MF05iVcgLWs"], []),
    ("05_gearbox_gear", ["FhDXOusMXS4"], []),
    ("06_daytona_shaft", ["l_fYJFZjbVE"], []),
    ("07_lathe_upgrade", ["5WitkgSPvRY"], []),
    ("08_bevel_gear", ["mm2j_c3vSuE"], []),
]


def main() -> int:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    log = OUTPUTS / "ironaxis_batch.jsonl"
    summary = []
    for name, ids, excluded in PROJECTS:
        folder = OUTPUTS / f"IronAxisLab_{name}"
        folder.mkdir(exist_ok=True)
        urls = [f"https://www.youtube.com/watch?v={video_id}" for video_id in ids]
        def progress(message: str) -> None:
            print(f"[{name}] {message}", flush=True)
            with log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "project": name,
                                         "message": message}, ensure_ascii=False) + "\n")
        try:
            ready = (folder / "final_us_documentary_1080p60.mp4").is_file() and all(
                (folder / "shorts" / short).is_file() for short in
                ("short_01_roughing.mp4", "short_02_precision.mp4", "short_03_final_reveal.mp4"))
            if not ready:
                # Two-source documentaries may naturally run longer than a single-source 600s target.
                target = 735 if len(ids) > 1 else 600
                result = produce_documentary(urls, niche_mode="engineering_machining",
                                             target_duration_sec=target, exclude_ranges=excluded,
                                             make_intro_hook=True, make_shorts=True,
                                             existing_project_dir=folder, progress=progress)
                if not result["self_verify_output"]["ok"]:
                    raise RuntimeError(f"Pipeline verification failed: {result['self_verify_output']['errors']}")
            verification = self_verify_output(folder, hook_duration=8.0, require_shorts=True)
            if not verification["ok"]:
                raise RuntimeError(f"Verification failed: {verification['errors']}")
            row = {"project": name, "status": "ok", "folder": str(folder),
                   "master_sec": verification["master_duration_sec"],
                   "shorts": len(verification["shorts"]),
                   "intro_bgm_dbfs": verification["intro_bgm_dbfs"],
                   "reused_verified_outputs": ready}
        except Exception as exc:
            row = {"project": name, "status": "failed", "folder": str(folder), "error": str(exc)}
            progress(traceback.format_exc())
        summary.append(row)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), **row}, ensure_ascii=False) + "\n")
        print("RESULT " + json.dumps(row, ensure_ascii=False), flush=True)
    print("\nPROJECT | STATUS | SHORTS | INTRO BGM dBFS | DETAILS", flush=True)
    for row in summary:
        print(f"{row['project']} | {row['status']} | {row.get('shorts', 0)} | "
              f"{row.get('intro_bgm_dbfs', '—')} | {row.get('error', row['folder'])}", flush=True)
    return 0 if all(row["status"] == "ok" for row in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
