import queue
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from app import App
from automation import SendResult, Target


class AppTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.targets = [Target(123, 99, "Fixture", (1, 2))]
        self.app.windows.configure(values=["Fixture"])
        self.app.windows.current(0)

    def tearDown(self):
        self.app.cleanup_worker()
        self.root.destroy()

    @patch("app.time.time", return_value=100)
    def test_arm_and_cancel(self, clock):
        self.app.arm()
        self.assertEqual(self.app.timer.deadline, 10900)
        self.assertEqual(str(self.app.cancel["state"]), "normal")
        self.app.cancel_timer()
        self.assertIsNone(self.app.timer.deadline)
        self.assertIn("Nothing was sent", self.app.status.get())

    @patch("app.time.time")
    @patch.object(App, "launch_worker")
    def test_due_launches_exactly_one_send(self, launch, clock):
        clock.return_value = 100
        self.app.arm()
        clock.return_value = 10900
        self.app.tick()
        self.app.tick()
        launch.assert_called_once_with("send")

    @patch("app.time.time")
    @patch.object(App, "launch_worker")
    def test_cancel_prevents_submission(self, launch, clock):
        clock.return_value = 100
        self.app.arm()
        self.app.cancel_timer()
        clock.return_value = 20000
        self.app.tick()
        launch.assert_not_called()

    @patch("app.messagebox.showerror")
    def test_invalid_input_does_not_arm(self, showerror):
        self.app.values[0].set("invalid")
        self.app.arm()
        self.assertIsNone(self.app.timer.deadline)
        showerror.assert_called_once()

    def test_discovery_populates_picker(self):
        self.app.operation = "discover"
        self.app.finish_worker("ok", self.app.targets)
        self.assertEqual(self.app.windows.current(), 0)
        self.assertEqual(str(self.app.start["state"]), "normal")

    def test_empty_discovery_disables_start(self):
        self.app.operation = "discover"
        self.app.finish_worker("ok", [])
        self.assertEqual(str(self.app.start["state"]), "disabled")
        self.assertIn("No accessible", self.app.status.get())

    def test_worker_error_is_visible(self):
        self.app.finish_worker("error", "Fixture failure")
        self.assertEqual(self.app.status.get(), "Fixture failure")

    def test_credit_blocked_codex_is_selectable_and_explained(self):
        target = Target(123, 99, "ChatGPT", (1, 2), app_name="Codex",
                        unavailable_reason="Out of credits. Nothing typed or sent.")
        self.app.operation = "discover"
        self.app.finish_worker("ok", [target])
        self.assertEqual(str(self.app.start["state"]), "normal")
        self.assertIn("Out of credits", self.app.status.get())
        self.assertIn("Codex", self.app.windows.get())

    def test_blocked_send_is_not_reported_as_submitted(self):
        self.app.operation = "send"
        self.app.finish_worker("ok", SendResult("blocked", "Out of credits. Nothing typed or sent."))
        self.assertIn("Out of credits", self.app.status.get())
        self.assertNotIn("Submitted", self.app.status.get())

    @patch("app.time.monotonic", return_value=40)
    def test_hung_worker_is_terminated_and_error_visible(self, clock):
        self.app.process = Mock()
        process = self.app.process
        self.app.queue = Mock()
        self.app.queue.get_nowait.side_effect = queue.Empty()
        self.app.worker_started = 0
        self.app.tick()
        process.terminate.assert_called_once()
        self.assertIn("timed out", self.app.status.get())


if __name__ == "__main__":
    unittest.main()
