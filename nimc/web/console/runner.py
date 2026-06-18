"""Run an action as a subprocess and stream its output as Server-Sent Events.

Stream protocol (unchanged from the original prototype)::

    event: started   data: <run_id>
    (default)        data: <one line of output>
    event: done      data: exit_code=<n>
    event: error     data: <message>

Two production concerns are handled here:

* **Reaping the whole subtree.** Each run starts in its own process group
  (``start_new_session=True``) so a single signal reaches ``nimc`` *and* its
  children (``ssh``, ``curl`` ...).
* **Multiple workers.** Gunicorn runs many workers, so the request that streams
  a run and the request that stops it may hit different processes. The in-memory
  registry only sees runs from the current worker, so each run also writes a
  pidfile (``<slug>__<run_id>.pid``). ``stop_run`` falls back to it, and it
  doubles as the cross-worker "is this server busy?" signal.
"""

import asyncio
import os
import signal
import tempfile
import uuid
from pathlib import Path

from .registry import Action

RUNTIME_DIR = Path(
    os.environ.get(
        "NIMC_CONSOLE_RUNTIME_DIR", Path(tempfile.gettempdir()) / "nimc-console"
    )
)

# Fast path for the common single-worker / dev case: run_id -> process.
_processes: dict[str, asyncio.subprocess.Process] = {}


def _runtime_dir() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR


def _pidfile(slug: str, run_id: str) -> Path:
    return _runtime_dir() / f"{slug}__{run_id}.pid"


def _find_pidfile(run_id: str) -> Path | None:
    return next(_runtime_dir().glob(f"*__{run_id}.pid"), None)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def server_busy(slug: str) -> bool:
    """True if a run for this server is still alive (across all workers)."""
    for pf in _runtime_dir().glob(f"{slug}__*.pid"):
        try:
            pid = int(pf.read_text())
        except (ValueError, OSError):
            pf.unlink(missing_ok=True)
            continue
        if _pid_alive(pid):
            return True
        pf.unlink(missing_ok=True)  # prune stale pidfile
    return False


async def _terminate(process: asyncio.subprocess.Process, sig: int) -> None:
    """Signal the run's whole process group, escalating to SIGKILL if needed."""
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def stream_action(slug: str, action: Action):
    """Run ``action`` and yield SSE events. Used as a StreamingResponse body."""
    run_id = uuid.uuid4().hex

    try:
        process = await asyncio.create_subprocess_exec(
            *action.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge stderr into the stream
            start_new_session=True,  # own process group for clean signalling
            env={**os.environ, "TERM": "xterm-256color"},
        )
    except FileNotFoundError:
        yield _sse("error", f"Command not found: {action.argv[0]}")
        return

    _processes[run_id] = process
    pidfile = _pidfile(slug, run_id)
    pidfile.write_text(str(process.pid))

    # Tell the client its run_id so it can POST /stop/{run_id}.
    yield _sse("started", run_id)

    assert process.stdout is not None
    try:
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            # SSE data lines cannot contain raw newlines.
            yield f"data: {text.replace(chr(10), chr(92) + 'n')}\n\n"

        await process.wait()
        yield _sse("done", f"exit_code={process.returncode}")
    finally:
        _processes.pop(run_id, None)
        if process.returncode is None:
            # The client disconnected mid-run (GeneratorExit). Kill log tails;
            # let lifecycle commands finish detached so a refresh doesn't abort
            # a create/destroy. Their pidfile stays so /stop can still find it.
            if action.kill_on_disconnect:
                await _terminate(process, signal.SIGINT)
                pidfile.unlink(missing_ok=True)
        else:
            pidfile.unlink(missing_ok=True)


def stop_run(run_id: str, sig: int = signal.SIGINT) -> bool:
    """Signal a running action by run_id. Returns False if it cannot be found."""
    process = _processes.get(run_id)
    if process is not None and process.returncode is None:
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except ProcessLookupError:
            return False
        return True

    # Different worker than the one streaming: use the pidfile.
    pidfile = _find_pidfile(run_id)
    if pidfile is None:
        return False
    try:
        pid = int(pidfile.read_text())
    except (ValueError, OSError):
        pidfile.unlink(missing_ok=True)
        return False
    try:
        os.killpg(os.getpgid(pid), sig)
    except ProcessLookupError:
        pidfile.unlink(missing_ok=True)
        return False
    return True
