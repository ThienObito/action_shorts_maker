"""Re-render an existing project through the shared documentary pipeline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from long_docu_pipeline import produce_documentary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--make-intro-hook", action="store_true")
    parser.add_argument("--make-shorts", action="store_true")
    parser.add_argument("--clean-outro-sec", type=float, default=0.0)
    parser.add_argument("--target-duration-sec", type=int, default=None)
    parser.add_argument("--reuse-script", action="store_true")
    args = parser.parse_args()
    folder = args.project_dir.resolve()
    metadata = json.loads((folder / "source_metadata.json").read_text(encoding="utf-8"))
    script = json.loads((folder / "script.json").read_text(encoding="utf-8")) if args.reuse_script else None
    urls = [item.get("url", "") for item in metadata if item.get("url")]
    if not urls:
        raise ValueError("source_metadata.json contains no source URLs")
    duration = args.target_duration_sec or int(float(metadata[0].get("duration") or 600)
                                                - args.clean_outro_sec)
    result = produce_documentary(urls, niche_mode="engineering_machining",
                                 target_duration_sec=duration,
                                 clean_outro_sec=args.clean_outro_sec,
                                 make_intro_hook=args.make_intro_hook,
                                 make_shorts=args.make_shorts, progress=print,
                                 existing_project_dir=folder, script_override=script)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
