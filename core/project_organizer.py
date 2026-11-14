"""Windows-safe, per-title output folders and inventory."""
import json
import re
from pathlib import Path

from config import OUTPUTS


def sanitize_title(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', title)
    name = re.sub(r'[^\w. -]+', '', name, flags=re.UNICODE)
    name = re.sub(r'[\s_]+', '_', name).strip(' ._')[:65].rstrip(' ._')
    if not name:
        name = 'Untitled_Video'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                      *(f'COM{i}' for i in range(1, 10)),
                                      *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name[:64]
    return name


def project_folder(title: str, video_id: str = '') -> Path:
    base = OUTPUTS / sanitize_title(title)
    if base.exists() and video_id:
        info = base / 'project_info.json'
        if info.exists() and json.loads(info.read_text(encoding='utf-8')).get('video_id') not in ('', video_id):
            base = OUTPUTS / (sanitize_title(title)[:50] + '_' + sanitize_title(video_id))
    base.mkdir(parents=True, exist_ok=True)
    return base


def list_completed_projects() -> list[dict]:
    return [{'title_folder': p.name, 'assets': [str(f.resolve()) for f in sorted(p.iterdir()) if f.is_file()]}
            for p in sorted(OUTPUTS.iterdir()) if p.is_dir() and (p / 'project_info.json').exists()]
