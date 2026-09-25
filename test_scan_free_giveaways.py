import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if SCRIPTS_DIR.is_dir():
    sys.path.insert(0, str(SCRIPTS_DIR))

import scan_free_giveaways as hunter


class DlcParentResolutionTests(unittest.TestCase):
    def test_catalog_resolved_owned_dlc_is_not_recommended(self):
        source = {
            "id": 1,
            "title": "Foo - Story DLC",
            "type": "DLC",
            "platforms": "PC, Steam",
            "description": "A story expansion for Foo. The base game Foo is required.",
            "instructions": "Download this DLC directly via Steam.",
            "end_date": "N/A",
            "status": "Active",
        }
        base_metadata = {
            "appid": 100,
            "checked": True,
            "reachable": True,
            "verification": "not_free_in_ru",
            "steam_type": "game",
            "steam_name": "Foo",
            "dlc": [200],
            "fullgame": None,
        }
        dlc_match = {
            "appid": 200,
            "name": "Foo - Story DLC",
            "matched_product_role": "candidate_product",
        }
        dlc_metadata = {
            "appid": 200,
            "checked": True,
            "reachable": True,
            "verification": "strong_keep_forever_candidate",
            "steam_type": "dlc",
            "steam_name": "Foo - Story DLC",
            "fullgame": {"appid": 100, "name": "Foo"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_file = root / "library.json"
            owned_dlc_file = root / "owned_dlc.json"
            taste_file = root / "taste_profile.json"
            history_file = root / "giveaway_history.json"
            giveaways_file = root / "giveaways.json"
            matches_file = root / "giveaway_matches.json"

            library_file.write_text(
                json.dumps({"games": [{"appid": 100, "name": "Foo"}]}),
                encoding="utf-8",
            )
            owned_dlc_file.write_text(
                json.dumps({"items": [{"appid": 200, "owned": True}]}),
                encoding="utf-8",
            )
            taste_file.write_text("{}", encoding="utf-8")

            with patch.multiple(
                hunter,
                LIBRARY_FILE=library_file,
                OWNED_DLC_FILE=owned_dlc_file,
                TASTE_FILE=taste_file,
                HISTORY_FILE=history_file,
                GIVEAWAYS_FILE=giveaways_file,
                MATCHES_FILE=matches_file,
            ), patch.object(hunter, "http_json", return_value=[source]), patch.object(
                hunter,
                "resolve_steam_match",
                return_value={"appid": 100, "name": "Foo"},
            ), patch.object(
                hunter, "inspect_steam_ru", return_value=base_metadata
            ), patch.object(
                hunter,
                "resolve_dlc_from_base_catalog",
                return_value=(dlc_match, dlc_metadata),
            ), patch.object(hunter.time, "sleep", return_value=None):
                hunter.main()

            matches = json.loads(matches_file.read_text(encoding="utf-8"))
            giveaways = json.loads(giveaways_file.read_text(encoding="utf-8"))
            history = json.loads(history_file.read_text(encoding="utf-8"))

        self.assertEqual(0, matches["match_count"])
        self.assertEqual([], matches["items"])
        self.assertEqual(
            {"owned": True, "reason": "appid_match", "appid": 200},
            giveaways["items"][0]["ownership"],
        )
        self.assertEqual("skip", history["items"]["gamerpower:1"]["last_band"])
        self.assertEqual(
            "already_owned",
            history["items"]["gamerpower:1"]["last_verification"],
        )
        self.assertEqual(
            "appid_match",
            history["items"]["gamerpower:1"]["last_ownership_reason"],
        )

    def test_catalog_rejects_dlc_whose_fullgame_is_a_different_base(self):
        candidate = {
            "title": "DAVE THE DIVER - Godzilla Content Pack",
            "type": "DLC",
            "description": "Godzilla content for DAVE THE DIVER.",
        }
        dave_base_metadata = {"steam_name": "DAVE THE DIVER", "dlc": [4099830]}
        wrong_dlc_metadata = {
            "steam_type": "dlc",
            "steam_name": "Magicraft - DAVE THE DIVER Content Pack",
            "fullgame": {"appid": 2103140, "name": "Magicraft"},
        }

        with patch.object(hunter, "inspect_steam_ru", return_value=wrong_dlc_metadata), patch.object(
            hunter.time, "sleep", return_value=None
        ):
            match, metadata = hunter.resolve_dlc_from_base_catalog(
                candidate, dave_base_metadata
            )

        self.assertIsNone(match)
        self.assertIsNone(metadata)

    def test_catalog_rejects_matching_parent_name_with_wrong_appid(self):
        candidate = {
            "title": "Foo - Story DLC",
            "type": "DLC",
            "description": "Story content for Foo.",
        }
        foo_base_metadata = {"appid": 100, "steam_name": "Foo", "dlc": [300]}
        wrong_parent_metadata = {
            "appid": 300,
            "steam_type": "dlc",
            "steam_name": "Foo - Story DLC",
            "fullgame": {"appid": 200, "name": "Foo"},
        }

        with patch.object(hunter, "inspect_steam_ru", return_value=wrong_parent_metadata), patch.object(
            hunter.time, "sleep", return_value=None
        ):
            match, metadata = hunter.resolve_dlc_from_base_catalog(
                candidate, foo_base_metadata
            )

        self.assertIsNone(match)
        self.assertIsNone(metadata)

    def test_ownership_gate_rejects_a_mismatched_fullgame_relation(self):
        candidate = {
            "title": "DAVE THE DIVER - Godzilla Content Pack",
            "type": "DLC",
            "steam_ru": {
                "steam_type": "dlc",
                "fullgame": {"appid": 2103140, "name": "Magicraft"},
            },
        }

        eligible = hunter.apply_dlc_ownership_gate(candidate, {2103140})

        self.assertFalse(eligible)
        self.assertIsNone(candidate["dlc_gate"]["eligible"])
        self.assertEqual("base_game_name_mismatch", candidate["dlc_gate"]["reason"])

    def test_ownership_gate_rejects_matching_parent_name_with_wrong_appid(self):
        candidate = {
            "title": "Foo - Story DLC",
            "type": "DLC",
            "base_game": {"appid": 100, "name": "Foo"},
            "steam_match": {"appid": 300, "matched_product_role": "candidate_product"},
            "steam_ru": {
                "appid": 300,
                "steam_type": "dlc",
                "fullgame": {"appid": 200, "name": "Foo"},
            },
        }

        eligible = hunter.apply_dlc_ownership_gate(candidate, {100, 200})

        self.assertFalse(eligible)
        self.assertIsNone(candidate["dlc_gate"]["eligible"])
        self.assertEqual("base_game_appid_mismatch", candidate["dlc_gate"]["reason"])

    def test_owned_base_without_resolved_dlc_stays_unresolved(self):
        candidate = {
            "title": "Foo - Missing Story DLC",
            "type": "DLC",
            "content_kind": "gameplay_dlc",
            "delivery": "external_steam_key",
            "key_region_status": "unknown_no_explicit_ru_block_seen",
            "base_game": {"appid": 100, "name": "Foo", "owned": True},
            "steam_match": {"appid": 100, "matched_product_role": "base_game_only"},
            "steam_ru": {
                "checked": False,
                "verification": "needs_review",
                "reason": "base_game_found_but_dlc_appid_unresolved",
            },
            "description": "Unlock story progression.",
        }

        eligible = hunter.apply_dlc_ownership_gate(candidate, {100})
        score, positives, negatives, concepts = hunter.taste_score(
            candidate,
            {"signals": {"very_strong_positive": ["meaningful_system_progression"]}},
        )
        band = hunter.recommendation_band(candidate, score)

        self.assertFalse(eligible)
        self.assertIsNone(candidate["dlc_gate"]["eligible"])
        self.assertEqual("dlc_product_unresolved", candidate["dlc_gate"]["reason"])
        self.assertEqual((0, [], [], []), (score, positives, negatives, concepts))
        self.assertEqual("needs_review", band)


class DlcTasteGateTests(unittest.TestCase):
    def setUp(self):
        self.taste = {
            "signals": {"very_strong_positive": ["meaningful_system_progression"]},
            "genre_preferences": {},
        }

    def test_cosmetic_unlock_does_not_count_as_progression(self):
        candidate = {
            "title": "EVERSPACE 2 Decal DLC",
            "type": "DLC",
            "description": "Unlock the Alienware decal for your ships.",
            "content_kind": "cosmetic_or_promo_dlc",
            "dlc_gate": {"eligible": True, "reason": "base_game_owned"},
            "steam_ru": {},
        }

        score, positives, negatives, concepts = hunter.taste_score(
            candidate, self.taste
        )

        self.assertEqual(0, score)
        self.assertNotIn("meaningful_system_progression", positives)
        self.assertEqual([], concepts)

    def test_unresolved_base_blocks_taste_scoring_before_band_selection(self):
        candidate = {
            "title": "Unknown Story Expansion",
            "type": "DLC",
            "description": "Unlock upgrades through meaningful progression.",
            "content_kind": "gameplay_dlc",
            "dlc_gate": {"eligible": None, "reason": "base_game_unresolved"},
            "steam_ru": {},
        }

        score, positives, negatives, concepts = hunter.taste_score(
            candidate, self.taste
        )

        self.assertEqual(0, score)
        self.assertEqual([], positives)
        self.assertEqual([], negatives)
        self.assertEqual([], concepts)

    def test_unresolved_dlc_cannot_rank_above_needs_review(self):
        candidate = {
            "type": "DLC",
            "delivery": "external_steam_key",
            "key_region_status": "unknown_no_explicit_ru_block_seen",
            "content_kind": "cosmetic_or_promo_dlc",
            "dlc_gate": {"eligible": None, "reason": "base_game_unresolved"},
            "steam_ru": {"verification": "needs_review"},
        }

        self.assertEqual("needs_review", hunter.recommendation_band(candidate, 99))

    def test_cosmetic_content_pack_is_not_promoted_by_unlock_wording(self):
        source = {
            "title": "Foo Cosmetic Content Pack",
            "description": "Unlock a decal for your ship.",
            "instructions": "Claim the DLC.",
            "type": "DLC",
            "platforms": "PC, Steam",
        }

        kind = hunter.classify_content(source)
        candidate = hunter.normalize_gamerpower(source)
        candidate["dlc_gate"] = {"eligible": True, "reason": "base_game_owned"}
        candidate["steam_ru"] = {}
        score, positives, _, concepts = hunter.taste_score(candidate, self.taste)

        self.assertEqual("cosmetic_or_promo_dlc", kind)
        self.assertEqual(0, score)
        self.assertNotIn("meaningful_system_progression", positives)
        self.assertEqual([], concepts)

    def test_classic_cosmetic_does_not_match_gameplay_class_marker(self):
        source = {
            "title": "Foo Classic Skin Pack",
            "description": "Unlock a classic cosmetic skin for your ship.",
            "instructions": "Claim the DLC.",
            "type": "DLC",
            "platforms": "PC, Steam",
        }

        kind = hunter.classify_content(source)
        candidate = hunter.normalize_gamerpower(source)
        candidate["dlc_gate"] = {"eligible": True, "reason": "base_game_owned"}
        candidate["steam_ru"] = {}
        score, positives, _, concepts = hunter.taste_score(candidate, self.taste)

        self.assertEqual("cosmetic_or_promo_dlc", kind)
        self.assertEqual(0, score)
        self.assertNotIn("meaningful_system_progression", positives)
        self.assertEqual([], concepts)


class SteamVerificationTests(unittest.TestCase):
    def test_discounted_paid_game_is_not_rejected_when_final_amount_is_stale(self):
        payload = {
            "2019300": {
                "success": True,
                "data": {
                    "type": "game",
                    "name": "Dokimon Quest",
                    "is_free": True,
                    "price_overview": {
                        "currency": "RUB",
                        "initial": 55000,
                        "final": 55000,
                        "discount_percent": 100,
                        "initial_formatted": "550 руб.",
                        "final_formatted": "Free",
                    },
                },
            }
        }

        with patch.object(hunter, "http_json", return_value=payload):
            result = hunter.inspect_steam_ru(2019300)

        self.assertEqual("strong_keep_forever_candidate", result["verification"])
        self.assertEqual(
            "paid_app_at_100_percent_discount_in_ru", result["reason"]
        )


class SourceIntegrityTests(unittest.TestCase):
    def test_http_json_retries_transient_server_failure(self):
        error = urllib.error.HTTPError(
            hunter.STEAM_SEARCH_URL, 502, "bad gateway", {}, io.BytesIO()
        )
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'

        with patch.object(
            hunter.urllib.request, "urlopen", side_effect=[error, response]
        ) as urlopen, patch.object(hunter.time, "sleep") as sleep:
            payload = hunter.http_json(hunter.STEAM_SEARCH_URL)

        self.assertEqual({"ok": True}, payload)
        self.assertEqual(2, urlopen.call_count)
        sleep.assert_called_once_with(3)

    def test_http_json_does_not_retry_forbidden_response(self):
        error = urllib.error.HTTPError(
            hunter.GAMERPOWER_URL, 403, "forbidden", {}, io.BytesIO()
        )
        with patch.object(
            hunter.urllib.request, "urlopen", side_effect=error
        ) as urlopen, patch.object(hunter.time, "sleep") as sleep:
            with self.assertRaises(hunter.SourceRequestError) as caught:
                hunter.http_json(hunter.GAMERPOWER_URL)

        self.assertEqual("forbidden", caught.exception.category)
        self.assertEqual(1, urlopen.call_count)
        sleep.assert_not_called()

    def test_http_json_classifies_gamerpower_http_failure(self):
        error = urllib.error.HTTPError(
            hunter.GAMERPOWER_URL, 429, "rate limited", {}, io.BytesIO()
        )
        with patch.object(
            hunter.urllib.request, "urlopen", side_effect=error
        ), patch.object(hunter.time, "sleep"):
            with self.assertRaises(hunter.SourceRequestError) as caught:
                hunter.http_json(hunter.GAMERPOWER_URL)

        self.assertEqual("gamerpower", caught.exception.source)
        self.assertEqual("rate_limited", caught.exception.category)

    def test_http_json_classifies_steam_malformed_json(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"not-json"
        with patch.object(hunter.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(hunter.SourceRequestError) as caught:
                hunter.http_json(hunter.STEAM_SEARCH_URL)

        self.assertEqual("steam", caught.exception.source)
        self.assertEqual("malformed_response", caught.exception.category)

    def test_steam_search_failure_is_not_returned_as_no_match(self):
        with patch.object(
            hunter, "steam_search", side_effect=RuntimeError("Steam Search 429")
        ):
            with self.assertRaisesRegex(RuntimeError, "Steam Search 429"):
                hunter.best_steam_match_for_queries(["Foo", "Foo Game"])

    def test_steam_search_failure_marks_partial_results_degraded(self):
        source = {
            "id": 99,
            "title": "Foo Adventure",
            "type": "Game",
            "platforms": "PC, Steam",
            "description": "Explore a story-rich world.",
            "instructions": "Claim the game.",
            "end_date": "N/A",
            "status": "Active",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
            }
            output_paths = {
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }

            with patch.multiple(hunter, **input_paths, **output_paths), patch.object(
                hunter, "http_json", return_value=[source]
            ), patch.object(
                hunter,
                "resolve_steam_match",
                side_effect=hunter.SourceRequestError(
                    "steam",
                    "http_error",
                    "steam request failed with HTTP 502.",
                    status=502,
                ),
            ):
                hunter.main()

            giveaways = json.loads(
                output_paths["GIVEAWAYS_FILE"].read_text(encoding="utf-8")
            )
            matches = json.loads(
                output_paths["MATCHES_FILE"].read_text(encoding="utf-8")
            )

        expected_error = {
            "source": "steam",
            "operation": "search",
            "category": "http_error",
            "message": "steam request failed with HTTP 502.",
            "status": 502,
            "source_id": 99,
            "title": "Foo Adventure",
        }
        for payload in (giveaways, matches):
            self.assertTrue(payload["run_degraded"])
            self.assertEqual([expected_error], payload["source_errors"])

        candidate = giveaways["items"][0]
        self.assertIsNone(candidate["steam_match"])
        self.assertEqual(
            {
                "checked": False,
                "verification": "unknown",
                "reason": "steam_search_request_error:http_error",
            },
            candidate["steam_ru"],
        )
        self.assertEqual("needs_review", matches["items"][0]["taste"]["band"])

    def test_malformed_gamerpower_schema_fails_before_writing_empty_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
            }
            output_paths = {
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }

            with patch.multiple(hunter, **input_paths, **output_paths), patch.object(
                hunter, "http_json", return_value={"unexpected": "schema"}
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "GamerPower.*expected a list"
                ):
                    hunter.main()

            self.assertFalse(output_paths["GIVEAWAYS_FILE"].exists())
            self.assertFalse(output_paths["MATCHES_FILE"].exists())

    def test_malformed_gamerpower_list_items_fail_before_writing_empty_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
            }
            output_paths = {
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }

            with patch.multiple(hunter, **input_paths, **output_paths), patch.object(
                hunter, "http_json", return_value=["unexpected"]
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "GamerPower.*expected every item to be an object"
                ):
                    hunter.main()

            self.assertFalse(output_paths["GIVEAWAYS_FILE"].exists())
            self.assertFalse(output_paths["MATCHES_FILE"].exists())

    def test_steam_appdetails_failure_marks_partial_results_degraded(self):
        source = {
            "id": 99,
            "title": "Foo Adventure",
            "type": "Game",
            "platforms": "PC, Steam",
            "description": "Explore a story-rich world.",
            "instructions": "Claim the game.",
            "end_date": "N/A",
            "status": "Active",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
            }
            output_paths = {
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }

            with patch.multiple(hunter, **input_paths, **output_paths), patch.object(
                hunter, "http_json", return_value=[source]
            ), patch.object(
                hunter,
                "resolve_steam_match",
                return_value={"appid": 123, "name": "Foo Adventure"},
            ), patch.object(
                hunter,
                "inspect_steam_ru",
                side_effect=hunter.SourceRequestError(
                    "steam", "rate_limited", "Steam request failed with HTTP 429."
                ),
            ), patch.object(hunter.time, "sleep", return_value=None):
                hunter.main()

            giveaways = json.loads(
                output_paths["GIVEAWAYS_FILE"].read_text(encoding="utf-8")
            )
            matches = json.loads(
                output_paths["MATCHES_FILE"].read_text(encoding="utf-8")
            )

        for payload in (giveaways, matches):
            self.assertTrue(payload["run_degraded"])
            self.assertEqual(
                [{
                    "source": "steam",
                    "operation": "appdetails",
                    "category": "rate_limited",
                    "message": "Steam request failed with HTTP 429.",
                    "appid": 123,
                }],
                payload["source_errors"],
            )


class HistoryIntegrityTests(unittest.TestCase):
    def test_timestamp_only_snapshot_is_suppressed_before_daily_heartbeat(self):
        previous = {
            "schema_version": "0.7.0",
            "updated_at_utc": "2026-08-25T00:00:00+00:00",
            "items": {
                "gamerpower:1": {
                    "title": "Foo",
                    "first_seen": "2026-08-24T00:00:00+00:00",
                    "last_seen": "2026-08-25T00:00:00+00:00",
                    "last_band": "skip",
                }
            },
        }
        current = json.loads(json.dumps(previous))
        current["updated_at_utc"] = "2026-08-25T04:00:00+00:00"
        current["items"]["gamerpower:1"]["last_seen"] = (
            "2026-08-25T04:00:00+00:00"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "giveaway_history.json"
            path.write_text(json.dumps(previous), encoding="utf-8")

            written = hunter.save_snapshot(
                path, current, "2026-08-25T04:00:00+00:00", history=True
            )

            stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertFalse(written)
        self.assertEqual(previous, stored)

    def test_timestamp_only_snapshot_writes_daily_heartbeat(self):
        previous = {
            "schema_version": "0.7.0",
            "updated_at_utc": "2026-08-25T00:00:00+00:00",
            "items": [],
        }
        current = {
            **previous,
            "updated_at_utc": "2026-08-26T00:00:00+00:00",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "giveaways.json"
            path.write_text(json.dumps(previous), encoding="utf-8")

            written = hunter.save_snapshot(
                path, current, "2026-08-26T00:00:00+00:00"
            )

            stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(written)
        self.assertEqual(current, stored)

    def test_semantic_snapshot_change_writes_immediately(self):
        previous = {
            "schema_version": "0.7.0",
            "updated_at_utc": "2026-08-25T00:00:00+00:00",
            "candidate_count": 1,
            "items": [{"source_id": 1}],
        }
        current = {
            **previous,
            "updated_at_utc": "2026-08-25T04:00:00+00:00",
            "candidate_count": 2,
            "items": [{"source_id": 1}, {"source_id": 2}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "giveaways.json"
            path.write_text(json.dumps(previous), encoding="utf-8")

            written = hunter.save_snapshot(
                path, current, "2026-08-25T04:00:00+00:00"
            )

            stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(written)
        self.assertEqual(current, stored)

    def test_prefilter_rejection_is_deduplicated_and_kept_current_in_history(self):
        source = {
            "id": 3486,
            "title": "Chop Shop Playtest",
            "type": "Early Access",
            "platforms": "PC, Steam",
            "description": "Join the playtest.",
            "instructions": "Request access on Steam.",
            "end_date": "N/A",
            "status": "Active",
        }
        first_seen = "2026-08-24T09:00:00+00:00"
        last_seen = "2026-08-25T09:00:00+00:00"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }

            with patch.multiple(hunter, **paths), patch.object(
                hunter, "http_json", return_value=[source]
            ), patch.object(
                hunter, "utc_now_iso", side_effect=[first_seen, last_seen]
            ):
                hunter.main()
                hunter.main()

            history = json.loads(
                paths["HISTORY_FILE"].read_text(encoding="utf-8")
            )

        self.assertEqual(["gamerpower:3486"], list(history["items"]))
        self.assertEqual(
            {
                "title": "Chop Shop Playtest",
                "first_seen": first_seen,
                "last_seen": last_seen,
                "last_band": "skip",
                "last_score": 0,
                "last_verification": "pre_filter_rejected",
                "last_filter_reason": "hard_reject:playtest",
                "last_end_date": "N/A",
                "delivery": "direct_or_unknown",
                "key_region_status": None,
                "content_kind": "game_or_other",
            },
            history["items"]["gamerpower:3486"],
        )

    def test_title_owned_candidate_is_deduplicated_and_kept_current_in_history(self):
        source = {
            "id": 99,
            "title": "Owned Adventure",
            "type": "Game",
            "platforms": "PC, Steam",
            "description": "Explore a story-rich world.",
            "instructions": "Claim the game on Steam.",
            "end_date": "N/A",
            "status": "Active",
        }
        first_seen = "2026-08-24T11:00:00+00:00"
        last_seen = "2026-08-25T11:00:00+00:00"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }
            paths["LIBRARY_FILE"].write_text(
                json.dumps({"games": [{"appid": 123, "name": "Owned Adventure"}]}),
                encoding="utf-8",
            )

            with patch.multiple(hunter, **paths), patch.object(
                hunter, "http_json", return_value=[source]
            ), patch.object(
                hunter, "utc_now_iso", side_effect=[first_seen, last_seen]
            ):
                hunter.main()
                hunter.main()

            history = json.loads(
                paths["HISTORY_FILE"].read_text(encoding="utf-8")
            )

        self.assertEqual(["gamerpower:99"], list(history["items"]))
        record = history["items"]["gamerpower:99"]
        self.assertEqual(first_seen, record["first_seen"])
        self.assertEqual(last_seen, record["last_seen"])
        self.assertEqual("skip", record["last_band"])
        self.assertEqual(0, record["last_score"])
        self.assertEqual("already_owned", record["last_verification"])
        self.assertEqual("library_title_match", record["last_ownership_reason"])


class NonDlcRegressionTests(unittest.TestCase):
    def test_regular_game_scoring_is_unchanged_by_dlc_gate(self):
        candidate = {
            "title": "Story Explorer",
            "type": "Game",
            "description": "Explore a world full of lore.",
            "content_kind": "game_or_other",
            "steam_ru": {},
        }
        taste = {"signals": {"very_strong_positive": ["exploration_and_lore"]}}

        score, positives, negatives, concepts = hunter.taste_score(candidate, taste)

        self.assertEqual(5, score)
        self.assertEqual(["exploration_and_lore"], positives)
        self.assertEqual([], negatives)
        self.assertEqual("exploration_and_lore", concepts[0]["concept"])


if __name__ == "__main__":
    unittest.main()
