"""In-process background job execution for POST /forecast/calculate + GET /forecast/status/{job_id}.

A thread-pool executor with an in-memory job registry — deliberately not
Celery/Redis/RQ. Nothing in this project's workload needs distributed task
queuing (single-process, single-machine, jobs measured in seconds), and
pulling in a message broker would be infrastructure the spec's own MVP
guidance doesn't require. If this needs to scale across machines later, the
``JobManager`` interface here is the seam to swap in a real queue without
touching the router code.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from api.schemas import JobStatus

logger = logging.getLogger(__name__)


class JobManager:
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, JobStatus] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, fn: Callable[..., dict[str, Any]], *args, **kwargs) -> str:
        job_id = str(uuid.uuid4())
        now = dt.datetime.now(dt.timezone.utc)
        with self._lock:
            self._jobs[job_id] = JobStatus(job_id=job_id, status="pending", created_at=now)

        def _run():
            with self._lock:
                self._jobs[job_id].status = "running"
                self._jobs[job_id].started_at = dt.datetime.now(dt.timezone.utc)
            try:
                result = fn(*args, **kwargs)
                with self._lock:
                    self._jobs[job_id].status = "completed"
                    self._jobs[job_id].completed_at = dt.datetime.now(dt.timezone.utc)
                    self._jobs[job_id].result = result
            except Exception as e:  # noqa: BLE001 - report the failure via job status, don't crash the worker
                logger.exception("Job %s failed", job_id)
                with self._lock:
                    self._jobs[job_id].status = "failed"
                    self._jobs[job_id].completed_at = dt.datetime.now(dt.timezone.utc)
                    self._jobs[job_id].error = str(e)

        future = self._executor.submit(_run)
        with self._lock:
            self._futures[job_id] = future
        return job_id

    def get(self, job_id: str) -> JobStatus | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None


job_manager = JobManager()
