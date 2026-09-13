"""Regression tests for reliable Telegram delivery."""

from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

# Keep this unit test independent from installed dependencies.
requests = types.ModuleType("requests")
requests.RequestException = type("RequestException", (Exception,), {})
requests.ConnectionError = type("ConnectionError", (requests.RequestException,), {})
requests.Timeout = type("Timeout", (requests.RequestException,), {})
requests.HTTPError = type("HTTPError", (requests.RequestException,), {})
requests.Response = object
requests.post = Mock()
sys.modules["requests"] = requests

from telegram import send_messages  # noqa: E402


def response(status_code: int, *, retry_after: int | None = None) -> Mock:
    result = Mock(status_code=status_code)
    result.json.return_value = (
        {"parameters": {"retry_after": retry_after}} if retry_after else {}
    )
    if status_code >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(str(status_code))
    return result


class TelegramDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "channel"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch("telegram.time.sleep")
    @patch("telegram.requests.post")
    def test_retries_only_failed_chunk_after_connection_reset(self, post, sleep):
        post.side_effect = [
            response(200),
            requests.ConnectionError("connection reset"),
            response(200),
        ]

        send_messages(["first", "second"], parse_mode="HTML")

        self.assertEqual(post.call_count, 3)
        self.assertEqual(
            [item.kwargs["json"]["text"] for item in post.call_args_list],
            ["first", "second", "second"],
        )
        sleep.assert_has_calls([call(1.0), call(2.0)])

    @patch("telegram.time.sleep")
    @patch("telegram.requests.post")
    def test_honours_telegram_retry_after(self, post, sleep):
        post.side_effect = [response(429, retry_after=7), response(200)]

        send_messages(["message"])

        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(7.0)

    @patch("telegram.time.sleep")
    @patch("telegram.requests.post")
    def test_does_not_retry_permanent_client_error(self, post, sleep):
        post.return_value = response(400)

        with self.assertRaises(requests.HTTPError):
            send_messages(["message"])

        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
