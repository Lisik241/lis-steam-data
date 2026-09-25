import io
import socket
import unittest
import urllib.error
from unittest import mock

from scripts import update_steam_data as updater


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class FetchJsonErrorTests(unittest.TestCase):
    def assert_category(self, side_effect, expected):
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=side_effect):
            with self.assertRaises(updater.SteamRequestError) as caught:
                updater.fetch_json("https://example.invalid", attempts=1)
        self.assertEqual(caught.exception.category, expected)

    def test_distinguishes_unauthorized_forbidden_and_rate_limit(self):
        for status, category in (
            (401, "unauthorized"),
            (403, "forbidden"),
            (429, "rate_limited"),
        ):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://example.invalid", status, "failure", {}, io.BytesIO()
                )
                self.assert_category(error, category)

    def test_distinguishes_network_and_timeout(self):
        self.assert_category(urllib.error.URLError("offline"), "network")
        self.assert_category(
            urllib.error.URLError(socket.timeout("slow")), "timeout"
        )
        self.assert_category(TimeoutError("slow"), "timeout")

    def test_invalid_json_is_malformed_response(self):
        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            return_value=_Response(b"not-json"),
        ):
            with self.assertRaises(updater.SteamRequestError) as caught:
                updater.fetch_json("https://example.invalid", attempts=1)

        self.assertEqual(caught.exception.category, "malformed_response")


if __name__ == "__main__":
    unittest.main()
