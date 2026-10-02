"""Run media work off the API loop, with cooperative process cancellation."""
import asyncio
import contextvars
import subprocess
import threading
import time

_cancel_event = contextvars.ContextVar("media_cancel_event", default=None)


def check_cancelled():
    event = _cancel_event.get()
    if event and event.is_set():
        raise InterruptedError("Generation cancelled")


async def run_blocking(fn, *args, **kwargs):
    event = threading.Event()
    token = _cancel_event.set(event)
    try:
        task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            event.set()
            # Let the worker stop before its workspace is removed.
            try:
                await asyncio.shield(task)
            except Exception:
                pass
            raise
    finally:
        _cancel_event.reset(token)


def run_process(cmd, timeout=600):
    check_cancelled()
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        while True:
            check_cancelled()
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"Media operation exceeded {timeout}s")
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace")[-2400:] or "Media operation failed")
        return stdout
    except BaseException:
        process.terminate()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise
