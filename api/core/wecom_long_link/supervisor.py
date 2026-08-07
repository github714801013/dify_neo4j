from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def _celery_command() -> list[str]:
    queues = os.environ.get("CELERY_WORKER_QUEUES") or os.environ.get("CELERY_QUEUES", "")
    pool = os.environ.get("CELERY_WORKER_POOL") or os.environ.get("CELERY_WORKER_CLASS", "gevent")
    concurrency = os.environ.get("CELERY_WORKER_CONCURRENCY") or os.environ.get("CELERY_WORKER_AMOUNT", "1")
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "celery_entrypoint.celery",
        "worker",
        "-P",
        pool,
        "-c",
        concurrency,
        "--max-tasks-per-child",
        os.environ.get("MAX_TASKS_PER_CHILD", "50"),
        "--loglevel",
        os.environ.get("LOG_LEVEL", "INFO"),
        "--prefetch-multiplier=" + os.environ.get("CELERY_PREFETCH_MULTIPLIER", "1"),
    ]
    if queues:
        command.extend(["-Q", queues])
    return command


def _forward_signal(processes: list[subprocess.Popen], signum: int, _frame) -> None:
    for process in processes:
        if process.poll() is None:
            process.send_signal(signum)


def main() -> int:
    processes = [
        subprocess.Popen(_celery_command()),
        subprocess.Popen([sys.executable, "-m", "core.wecom_long_link.worker"]),
    ]
    shutting_down = False

    def handle_signal(signum: int, _frame) -> None:
        nonlocal shutting_down
        shutting_down = True
        _forward_signal(processes, signum, _frame)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        while True:
            statuses = [process.poll() for process in processes]
            if any(status is not None for status in statuses):
                if shutting_down and all(
                    status in {0, -signal.SIGTERM, -signal.SIGINT}
                    for status in statuses
                    if status is not None
                ):
                    return 0
                return next(status if status is not None and status != 0 else 1 for status in statuses)
            time.sleep(1)
    finally:
        _forward_signal(processes, signal.SIGTERM, None)
        for process in processes:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
