"""MCP stdio tools for automated shorts production (stdout reserved for JSON-RPC)."""
from __future__ import annotations

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / '.env', override=True)
_rpc_stdout = sys.stdout
sys.stdout = sys.stderr
os.environ['YOLO_VERBOSE'] = 'False'

import anyio
import json
import time
from threading import Thread
from datetime import datetime, timezone
from io import TextIOWrapper
from uuid import uuid4
from mcp.server.stdio import stdio_server

from mcp.server.fastmcp import FastMCP

from core.pipeline import (analyze_video_storyboard as _analyze,
                           process_youtube_links as _process)
from core.project_organizer import list_completed_projects as _list
from config import OUTPUTS
from long_docu_pipeline import produce_documentary

JOBS = OUTPUTS / 'jobs'
LOG = OUTPUTS / 'live_status.log'


def _record(job_id: str, status: str, percent: int, message: str, **extra) -> None:
    JOBS.mkdir(parents=True, exist_ok=True)
    entry = {'job_id': job_id, 'status': status, 'percent': percent,
             'message': message, 'updated': datetime.now(timezone.utc).isoformat(), **extra}
    path = JOBS / f'{job_id}.json'
    staged = JOBS / f'{job_id}.{uuid4().hex}.tmp'
    staged.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(10):
        try:
            staged.replace(path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.05)
    with LOG.open('a', encoding='utf-8') as file:
        file.write(f'[{percent}%] {job_id}: {message}\n')


def _work(job_id: str, urls: list[str] | str, mode: str, max_clips: int) -> list[dict]:
    def progress(percent: int, message: str) -> None:
        _record(job_id, 'running', percent, message)
    try:
        result = _process(urls, mode, max_clips, progress=progress)
        _record(job_id, 'completed', 100, 'Done!', result=result)
        return result
    except Exception as exc:
        _record(job_id, 'failed', 100, f'{type(exc).__name__}: {exc}')
        raise

server = FastMCP('action_shorts_maker')


@server.tool()
def process_youtube_links(urls: list[str] | str, mode: str = 'short', max_clips: int = 1,
                          background: bool = True) -> list[dict] | dict:
    """Queue production and return a job ID immediately; background=False waits for results."""
    if isinstance(urls, str):
        urls = [urls]
    if not urls or not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        raise ValueError('urls must be a nonempty list or a single URL')
    if mode not in ('short', 'both', 'classic59') or not 1 <= max_clips <= 9:
        raise ValueError('Invalid mode or max_clips')
    job_id = uuid4().hex[:12]
    _record(job_id, 'queued', 0, 'Queued', urls=urls, mode=mode)
    if background:
        def run_job() -> None:
            try:
                _work(job_id, urls, mode, max_clips)
            except Exception:
                # _work records failure; do not dump API responses into stderr.
                pass
        Thread(target=run_job, name=f'action-shorts-{job_id}', daemon=True).start()
        return {'job_id': job_id, 'status': 'queued', 'status_tool': 'get_job_status'}
    return _work(job_id, urls, mode, max_clips)


@server.tool()
def produce_us_documentary(youtube_urls: list[str], niche_mode: str = 'engineering_machining',
                           target_duration_sec: int = 600, background: bool = True,
                           clean_outro_sec: float = 0, voice: str = 'en-US-ChristopherNeural',
                           exclude_ranges: list[list[float]] | None = None,
                           make_intro_hook: bool = False, make_shorts: bool = True) -> dict:
    """Prepare original US-English documentary narration for cleared source footage."""
    if not youtube_urls or not all(isinstance(url, str) and url.startswith(('https://', 'http://'))
                                   for url in youtube_urls):
        raise ValueError('youtube_urls must be a nonempty list of HTTP(S) URLs')
    if niche_mode not in ('engineering_machining', 'woodworking_bonsai', 'survival_commentary'):
        raise ValueError('Unsupported niche_mode')
    if target_duration_sec < 60:
        raise ValueError('target_duration_sec must be at least 60')
    job_id = uuid4().hex[:12]
    _record(job_id, 'queued', 0, 'Queued documentary preparation', urls=youtube_urls,
            mode=niche_mode, target_duration_sec=target_duration_sec)

    def work() -> None:
        def progress(message: str) -> None:
            _record(job_id, 'running', 50, message)
        try:
            result = produce_documentary(youtube_urls, niche_mode, target_duration_sec, progress,
                                         clean_outro_sec=clean_outro_sec, voice=voice,
                                         exclude_ranges=[tuple(r) for r in (exclude_ranges or [])],
                                         make_intro_hook=make_intro_hook, make_shorts=make_shorts)
            _record(job_id, 'completed', 100, 'Documentary render complete', result=result)
        except Exception as exc:
            _record(job_id, 'failed', 100, f'{type(exc).__name__}: {exc}')

    if background:
        Thread(target=work, name=f'us-documentary-{job_id}', daemon=True).start()
        return {'job_id': job_id, 'status': 'queued', 'status_tool': 'get_job_status'}
    work()
    path = JOBS / f'{job_id}.json'
    return json.loads(path.read_text(encoding='utf-8'))


@server.tool()
def analyze_video_storyboard(url: str) -> dict:
    """Scan candidate physics, six-frame contact sheets, and live expert edit decisions."""
    return _analyze(url)


@server.tool()
def get_job_status(job_id: str | None = None) -> dict:
    """Read live progress and active/completed jobs; pass a job ID for one job."""
    JOBS.mkdir(parents=True, exist_ok=True)
    paths = [JOBS / f'{job_id}.json'] if job_id else sorted(JOBS.glob('*.json'))
    jobs = [json.loads(path.read_text(encoding='utf-8')) for path in paths if path.is_file()]
    return {'jobs': jobs, 'live_status_log': str(LOG),
            'active': [job for job in jobs if job['status'] in ('queued', 'running')],
            'completed': [job for job in jobs if job['status'] == 'completed'],
            'failed': [job for job in jobs if job['status'] == 'failed'],
            'recent_log': LOG.read_text(encoding='utf-8').splitlines()[-30:] if LOG.exists() else []}


@server.tool()
def list_completed_projects() -> list[dict]:
    """List finished title folders and all their generated asset paths."""
    return _list()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        _work(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5]))
    else:
        async def run_rpc() -> None:
            # The SDK normally grabs sys.stdout.buffer. That now points to stderr;
            # explicitly bind its writer to the saved JSON-RPC pipe instead.
            rpc = anyio.wrap_file(TextIOWrapper(_rpc_stdout.buffer, encoding='utf-8', write_through=True))
            async with stdio_server(stdout=rpc) as (reader, writer):
                await server._mcp_server.run(reader, writer,
                                             server._mcp_server.create_initialization_options())
        anyio.run(run_rpc)
