import unittest
from threading import Event, Thread

from dontanello.entrypoints.worker import Job, run_cycle, run_worker


class WorkerTests(unittest.TestCase):
    def test_failed_source_does_not_skip_other_jobs(self):
        executed = []

        def fail():
            raise RuntimeError("provider unavailable")

        def succeed():
            executed.append(True)
            return 0

        with self.assertLogs(level="ERROR"):
            self.assertFalse(run_cycle([Job("bad", fail), Job("good", succeed)]))
        self.assertEqual(executed, [True])

    def test_once_reports_failure(self):
        def fail():
            raise RuntimeError("provider unavailable")

        with self.assertLogs(level="ERROR"), self.assertRaises(RuntimeError):
            run_worker([Job("bad", fail)], interval=30, once=True)

    def test_slow_group_does_not_block_an_independent_group(self):
        stop, release, ran = Event(), Event(), Event()

        def slow():
            release.wait(2)
            return 0

        def fast():
            ran.set()
            release.set()
            stop.set()
            return 0

        thread = Thread(
            target=run_worker,
            args=(
                [
                    Job("slow", slow, group="slow"),
                    Job("fast", fast, group="fast"),
                ],
                1,
            ),
            kwargs={"stop_event": stop},
        )
        thread.start()
        try:
            self.assertTrue(ran.wait(1))
        finally:
            release.set()
            stop.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
