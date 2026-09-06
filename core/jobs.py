"""Background jobs and the auto-run ticker.

A full rebuild writes about 358,000 rows, so it cannot run inside a request.
Jobs run on a worker thread, collect their log lines as they go, and the page
polls for them.

The ticker is what makes the simulation feel live: it fires a churn cycle every
N seconds, and optionally reads the change feed after each one, so a trainee can
leave it running and watch the source move underneath a pipeline.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime

MAX_LINES = 600


@dataclass
class Job:
    id: str
    name: str
    status: str = "running"            # running | done | error
    lines: list[str] = field(default_factory=list)
    result: dict | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.lines.append(f"{stamp}  {message}")
            if len(self.lines) > MAX_LINES:
                del self.lines[:len(self.lines) - MAX_LINES]

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            lines = self.lines[since:]
            total = len(self.lines)
        return {
            "id": self.id, "name": self.name, "status": self.status,
            "lines": lines, "total_lines": total, "result": self.result,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
        }


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_current: dict[str, str] = {}          # name -> job id, so a page can find its job


def start(name: str, fn, *args, **kwargs) -> Job:
    """Run fn on a worker thread. fn receives a `log` callable as a keyword."""
    job = Job(id=uuid.uuid4().hex[:12], name=name)
    with _jobs_lock:
        _jobs[job.id] = job
        _current[name] = job.id
        if len(_jobs) > 40:                       # keep the newest 40
            for old in sorted(_jobs.values(), key=lambda j: j.started)[:-40]:
                _jobs.pop(old.id, None)

    def runner() -> None:
        from django.db import close_old_connections
        try:
            job.result = fn(*args, log=job.log, **kwargs)
            job.status = "done"
            job.log("finished")
        except Exception as exc:
            job.status = "error"
            job.result = {"error": f"{type(exc).__name__}: {exc}"}
            job.log(f"FAILED: {type(exc).__name__}: {exc}")
            for line in traceback.format_exc().splitlines()[-6:]:
                job.log(f"    {line}")
        finally:
            job.finished = time.time()
            close_old_connections()

    threading.Thread(target=runner, name=f"job-{name}", daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def current(name: str) -> Job | None:
    return _jobs.get(_current.get(name, ""))


def running() -> list[Job]:
    return [j for j in _jobs.values() if j.status == "running"]


# ---------------------------------------------------------------------------
# Auto-run ticker
# ---------------------------------------------------------------------------
class Ticker:
    """Fires a churn cycle on an interval, so the source systems keep moving."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.interval = 60
        self.scale = 1
        self.run_etl = False
        self.ticks = 0
        self.last_tick: str | None = None
        self.last_result: dict | None = None
        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, interval: int = 60, scale: int = 1, run_etl: bool = False) -> None:
        if self.is_running:
            self.interval, self.scale, self.run_etl = interval, scale, run_etl
            return
        self.interval = max(10, min(int(interval), 3600))
        self.scale = max(1, min(int(scale), 20))
        self.run_etl = bool(run_etl)
        self._stop.clear()
        self.ticks = 0
        self._thread = threading.Thread(target=self._loop, name="ticker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        from django.db import close_old_connections

        from etl import runner
        from systems import churn
        while not self._stop.is_set():
            close_old_connections()
            try:
                lines: list[str] = []
                result = churn.simulate(self.scale, log=lines.append)
                if self.run_etl:
                    result["etl"] = runner.run_all(log=lines.append)
                self.last_result = result
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
            self.ticks += 1
            self.last_tick = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._stop.wait(self.interval)
        self._thread = None

    def status(self) -> dict:
        return {
            "running": self.is_running, "interval": self.interval, "scale": self.scale,
            "run_etl": self.run_etl, "ticks": self.ticks, "last_tick": self.last_tick,
            "last_error": self.last_error,
        }


ticker = Ticker()
