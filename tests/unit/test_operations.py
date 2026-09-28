import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import sleep

from dontanello.modules.operations import ErrorMonitor
from dontanello.modules.operations.adapters.json_alerts import JsonAlertState
from dontanello.modules.operations.adapters.serialized_monitor import SerializedMonitor
from dontanello.modules.operations.adapters.telegram import TelegramAlertSender
from dontanello.modules.operations.models import AlertStatus


class MemoryState:
    def __init__(self):
        self.values = {}
        self.fail = False

    def get(self, job):
        if self.fail:
            raise OSError("storage detail")
        return self.values.get(job, AlertStatus())

    def set(self, job, status):
        if self.fail:
            raise OSError("storage detail")
        self.values[job] = status


class RecordingSender:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send(self, text):
        self.messages.append(text)
        if self.fail:
            raise RuntimeError("token must not leak")


class ErrorMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        self.sender = RecordingSender()
        self.state = MemoryState()
        self.monitor = ErrorMonitor(self.state, self.sender, cooldown_seconds=3600)

    def test_sends_first_failure_then_respects_cooldown(self):
        self.monitor.failed("reports", self.now)
        self.monitor.failed("reports", self.now + timedelta(minutes=59))
        self.assertEqual(len(self.sender.messages), 1)
        self.monitor.failed("reports", self.now + timedelta(hours=1))
        self.assertEqual(len(self.sender.messages), 2)
        self.assertEqual(self.state.values["reports"].notification_count, 2)
        self.assertIn("reports", self.sender.messages[0])

    def test_recovery_notifies_once_and_new_failure_starts_new_incident(self):
        self.monitor.failed("completion", self.now)
        self.monitor.recovered("completion", self.now + timedelta(minutes=1))
        self.monitor.recovered("completion", self.now + timedelta(minutes=2))
        self.assertEqual(len(self.sender.messages), 2)
        self.assertIn("восстановлена", self.sender.messages[1])
        self.monitor.failed("completion", self.now + timedelta(minutes=3))
        self.assertEqual(len(self.sender.messages), 3)

    def test_sender_failure_is_persisted_and_does_not_escape(self):
        self.sender.fail = True
        self.monitor.failed("reports", self.now)
        self.assertEqual(self.state.values["reports"].notification_count, 1)
        self.monitor.failed("reports", self.now + timedelta(minutes=1))
        self.assertEqual(self.state.values["reports"].notification_count, 1)
        self.assertNotIn("token must not leak", self.sender.messages[0])

    def test_state_failure_does_not_escape_or_send(self):
        self.state.fail = True
        self.monitor.failed("reports", self.now)
        self.monitor.recovered("reports", self.now)
        self.assertEqual(self.sender.messages, [])

    def test_restart_retains_cooldown(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.json"
            first_sender = RecordingSender()
            ErrorMonitor(JsonAlertState(path), first_sender).failed("reports", self.now)
            restarted_sender = RecordingSender()
            restarted = ErrorMonitor(JsonAlertState(path), restarted_sender)
            restarted.failed("reports", self.now + timedelta(minutes=30))
            self.assertEqual(restarted_sender.messages, [])
            restarted.failed("reports", self.now + timedelta(hours=1))
            self.assertEqual(len(restarted_sender.messages), 1)

    def test_failed_recovery_notice_retries_after_restart_and_cooldown(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.json"
            state = JsonAlertState(path)
            state.set("reports", AlertStatus(True, self.now, self.now, 1))
            failed_sender = RecordingSender()
            failed_sender.fail = True
            ErrorMonitor(state, failed_sender).recovered("reports", self.now + timedelta(minutes=1))
            self.assertTrue(JsonAlertState(path).get("reports").recovery_pending)

            successful_sender = RecordingSender()
            restarted = ErrorMonitor(JsonAlertState(path), successful_sender)
            restarted.recovered("reports", self.now + timedelta(minutes=59))
            self.assertEqual(successful_sender.messages, [])
            restarted.recovered("reports", self.now + timedelta(hours=1, minutes=1))
            restarted.recovered("reports", self.now + timedelta(hours=1, minutes=2))
            self.assertEqual(len(successful_sender.messages), 1)
            self.assertFalse(JsonAlertState(path).get("reports").recovery_pending)

    def test_telegram_adapter_forwards_only_configured_chat_and_text(self):
        class Client:
            def __init__(self):
                self.calls = []

            def send_message(self, chat_id, text):
                self.calls.append((chat_id, text))

        client = Client()
        TelegramAlertSender(client, "12345").send("generic failure")
        self.assertEqual(client.calls, [("12345", "generic failure")])

    def test_serialized_monitor_runs_concurrent_transitions_one_at_a_time(self):
        class ConcurrentProbe:
            def __init__(self):
                self.guard = Lock()
                self.active = 0
                self.maximum = 0

            def failed(self, job, now):
                with self.guard:
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                sleep(0.02)
                with self.guard:
                    self.active -= 1

            def recovered(self, job, now):
                self.failed(job, now)

        probe = ConcurrentProbe()
        monitor = SerializedMonitor(probe)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(monitor.failed, "reports", self.now) for _ in range(4)]
            for future in futures:
                future.result()
        self.assertEqual(probe.maximum, 1)


if __name__ == "__main__":
    unittest.main()
