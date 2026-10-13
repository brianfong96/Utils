import unittest
from unittest.mock import patch

from timer import Timer, countdown, duration_seconds


class TimerTests(unittest.TestCase):
    def test_default_three_hours(self):
        self.assertEqual(duration_seconds("3", "0", "0"), 10800)

    def test_duration_normalizes_large_minutes_and_whitespace(self):
        self.assertEqual(duration_seconds(" 0 ", "90", "1"), 5401)

    def test_invalid_duration(self):
        for fields in [("0", "0", "0"), ("-1", "0", "0"), ("1.5", "0", "0"),
                       ("", "0", "1"), ("169", "0", "0"), ("nan", "0", "0"),
                       ("0", "0", "²")]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                duration_seconds(*fields)

    def test_seven_day_limit(self):
        self.assertEqual(duration_seconds("168", "0", "0"), 604800)

    def test_countdown_rounds_up(self):
        self.assertEqual(countdown(3599.1), "01:00:00")
        self.assertEqual(countdown(-5), "00:00:00")
        self.assertEqual(countdown(10800), "03:00:00")

    @patch("timer.time.time")
    def test_due_is_consumed_once_including_resume(self, clock):
        clock.return_value = 100
        timer = Timer()
        timer.start(10800)
        clock.return_value = 200
        self.assertEqual(timer.remaining(), 10700)
        self.assertFalse(timer.take_due())
        clock.return_value = 20000
        self.assertTrue(timer.take_due())
        self.assertFalse(timer.take_due())

    @patch("timer.time.time", return_value=100)
    def test_cancellation_and_restart(self, clock):
        timer = Timer()
        timer.start(5)
        timer.cancel()
        clock.return_value = 1000
        self.assertFalse(timer.take_due())
        timer.start(1)
        clock.return_value = 1001
        self.assertTrue(timer.take_due())

    def test_invalid_programmatic_delay(self):
        for seconds in (0, -1, 604801):
            with self.assertRaises(ValueError):
                Timer().start(seconds)
