"""Bound memory-heavy jobs for a 16 GB / RTX 3070 workstation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import BoundedSemaphore, Event, Thread
import gc
import psutil

MAX_CONCURRENT_JOBS = 2
FFMPEG_THREADS = 6
MIN_AVAILABLE_RAM_BYTES = 2 * 1024 ** 3
_heavy = BoundedSemaphore(MAX_CONCURRENT_JOBS)


@contextmanager
def heavy_job():
    with _heavy:
        if psutil.virtual_memory().available < MIN_AVAILABLE_RAM_BYTES:
            raise MemoryError('Less than 2 GiB free RAM; declining heavy video job')
        stopped = Event()
        stats = {'peak_process_tree_rss_mb': 0.0}
        def sample():
            process = psutil.Process()
            while not stopped.is_set():
                try:
                    children = process.children(recursive=True)
                    rss = sum(p.memory_info().rss for p in [process, *children] if p.is_running())
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    rss = 0
                stats['peak_process_tree_rss_mb'] = max(stats['peak_process_tree_rss_mb'],
                                                       round(rss / 1024**2, 1))
                stopped.wait(.2)
        monitor = Thread(target=sample, daemon=True)
        monitor.start()
        try:
            yield stats
        finally:
            stopped.set()
            monitor.join(timeout=2)
            gc.collect()


def worker_pool():
    return ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS,
                              thread_name_prefix="action-shorts")
