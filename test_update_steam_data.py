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


class DlcFallbackTests(unittest.TestCase):
    def test_extracts_dlc_appids_from_store_page(self):
        html = b'<a href="https://store.steampowered.com/app/3022790/RimWorld__Odyssey/">Odyssey</a><a href="https://store.steampowered.com/app/294100/RimWorld/">Base</a>'
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=_Response(html)):
            self.assertEqual(updater.get_store_dlc_ids(294100), [3022790])


class DlcCatalogRateLimitTests(unittest.TestCase):
    def test_rate_limited_dlc_metadata_is_kept_as_partial_entry(self):
        library = [{"appid": 10, "name": "Base Game"}]
        base_details = {"dlc": [20]}
        rate_limit = updater.SteamRequestError(
            "rate_limited",
            "Steam request failed with HTTP 429.",
            retryable=True,
            status=429,
        )

        with mock.patch.object(
            updater, "get_app_details", side_effect=[base_details, rate_limit]
        ), mock.patch.object(updater.time, "sleep"), mock.patch(
            "pathlib.Path.write_text", autospec=True
        ) as write_text:
            updater.update_dlc_catalog(library, "2026-09-28T00:00:00+00:00")

        payload = __import__("json").loads(write_text.call_args.args[1])
        self.assertEqual(payload["total_dlc_count"], 1)
        self.assertEqual(payload["games"][0]["dlc"][0]["appid"], 20)
        self.assertFalse(payload["games"][0]["dlc"][0]["store_details_available"])


class GameDetailsRateLimitTests(unittest.TestCase):
    def test_rate_limited_game_details_do_not_abort_catalog(self):
        library = [{"appid": 10, "name": "Rate Limited Game"}]
        rate_limit = updater.SteamRequestError(
            "rate_limited",
            "Steam request failed with HTTP 429.",
            retryable=True,
            status=429,
        )

        with mock.patch.object(
            updater, "get_app_details", side_effect=rate_limit
        ), mock.patch.object(updater.time, "sleep"), mock.patch(
            "pathlib.Path.write_text", autospec=True
        ) as write_text:
            updater.update_dlc_catalog(library, "2026-09-29T00:00:00+00:00")

        payload = __import__("json").loads(write_text.call_args.args[1])
        game = payload["games"][0]
        self.assertEqual(game["game_appid"], 10)
        self.assertFalse(game["store_details_available"])
        self.assertEqual(game["dlc_count"], 0)
        self.assertEqual(game["dlc"], [])


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
