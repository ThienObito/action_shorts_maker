"""Generate VideoLingo config from project .env without printing credentials."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
base = os.environ.get("CODEX_BASE_URL") or os.environ.get("AI_API_BASE", "")
key = os.environ.get("CODEX_API_KEY") or os.environ.get("AI_API_KEY", "")
if not base or not key:
    raise SystemExit("Set CODEX_BASE_URL (or AI_API_BASE) and CODEX_API_KEY in .env")
# Restrict generated YAML to known values; quote safely via JSON-compatible YAML strings.
import json
content = "llm:\n  base_url: " + json.dumps(base.rstrip("/")) + "\n  api_key: " + json.dumps(key) + "\n  model: \"gpt-5.6-sol\"\n"
target = ROOT / "videolingo.config.yaml"
target.write_text(content, encoding="utf-8")
try:
    target.chmod(0o600)
except OSError:
    pass
print(f"Wrote {target}; keep credentials private and confirm VideoLingo schema before use.")
