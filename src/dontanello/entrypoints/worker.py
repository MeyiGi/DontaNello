"""Polling runner with a separate error boundary per configured source."""

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[], int]
    group: str = "default"
    interval_seconds: int = 0


def run_cycle(
    jobs: Sequence[Job],
    failed: Callable[[str], None] | None = None,
    recovered: Callable[[str], None] | None = None,
) -> bool:
    succeeded = True
    for job in jobs:
        try:
            count = job.run()
            if count:
                logging.info("%s: completed %d actions", job.name, count)
            if recovered:
                recovered(job.name)
        except Exception as error:
            logging.error("%s: %s", job.name, error)
            succeeded = False
            if failed:
                failed(job.name)
    return succeeded


def run_worker(
    jobs: Sequence[Job],
    interval: int,
    once: bool = False,
    failed: Callable[[str], None] | None = None,
    recovered: Callable[[str], None] | None = None,
    stop_event: Event | None = None,
) -> None:
    if once:
        # Emit outcomes before draining the notification group.
        ordered = sorted(jobs, key=lambda job: job.group == "alerts")
        if not run_cycle(ordered, failed, recovered):
            raise RuntimeError("Один или несколько циклов автоматизации завершились ошибкой")
        return
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.group, []).append(job)
    pending: dict[str, Future[bool]] = {}
    due: dict[str, float] = {}
    # Sources sharing JSON state remain serial within their group. Independent
    # network/backup/report jobs cannot block completion polling.
    with ThreadPoolExecutor(max_workers=max(1, len(groups))) as executor:
        while stop_event is None or not stop_event.is_set():
            now = time.monotonic()
            for name, group_jobs in groups.items():
                if name in pending:
                    if not pending[name].done():
                        continue
                    pending.pop(name).result()
                    cadence = min(job.interval_seconds or interval for job in group_jobs)
                    due[name] = now + cadence
                if now >= due.get(name, 0):
                    pending[name] = executor.submit(run_cycle, group_jobs, failed, recovered)
            if stop_event:
                stop_event.wait(min(1, interval))
            else:
                time.sleep(min(1, interval))
