#!/usr/bin/env python3
"""
Steam giveaway hunter v0.7.0 for Karai.

Main changes:
- DLC ownership chain comes before taste: if the required base game is not owned,
  the DLC is skipped as base_game_not_owned.
- DLC resolver can use Steam's fullgame relation and the base game's DLC catalog.
- Achievement bonus is counted once.
- Combat-loop-heavy games receive a penalty when no story/world/exploration/system
  hook is visible in available metadata.
- Permanent cosmetic/artbook/soundtrack DLC is no longer discarded merely for
  being bonus content; service/temporary/account rewards are still rejected.
- DLC matches must agree with Steam's fullgame parent before ownership or taste
  scoring is trusted.
- Unresolved DLC bases never receive taste points or rank above needs_review.
- Cosmetic/promo wording such as "unlock a decal" is not progression.
"""

import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LIBRARY_FILE = ROOT / "library.json"
OWNED_DLC_FILE = ROOT / "owned_dlc.json"
TASTE_FILE = ROOT / "taste_profile.json"

GIVEAWAYS_FILE = ROOT / "giveaways.json"
MATCHES_FILE = ROOT / "giveaway_matches.json"
HISTORY_FILE = ROOT / "giveaway_history.json"

GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways?platform=steam"
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

USER_AGENT = "KaraiSteamHunter/0.7.0"
REQUEST_DELAY_SECONDS = 0.35
SNAPSHOT_HEARTBEAT_SECONDS = 24 * 60 * 60


class SourceRequestError(RuntimeError):
    def __init__(self, source, category, message, status=None):
        super().__init__(message)
        self.source = source
        self.category = category
        self.status = status


def request_source(url):
    return "gamerpower" if "gamerpower.com" in url else "steam"


def classify_request_error(source, exc):
    if isinstance(exc, urllib.error.HTTPError):
        categories = {
            401: "unauthorized",
            403: "forbidden",
            429: "rate_limited",
        }
        category = categories.get(exc.code, "http_error")
        return SourceRequestError(
            source,
            category,
            f"{source} request failed with HTTP {exc.code}.",
            status=exc.code,
        )

    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return SourceRequestError(source, "timeout", f"{source} request timed out.")

    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, TimeoutError) or isinstance(exc.reason, socket.timeout):
            return SourceRequestError(source, "timeout", f"{source} request timed out.")
        return SourceRequestError(
            source,
            "network",
            f"{source} network request failed: {exc.reason}",
        )

    if isinstance(exc, (UnicodeDecodeError, json.JSONDecodeError)):
        return SourceRequestError(
            source,
            "malformed_response",
            f"{source} returned malformed JSON.",
        )

    return SourceRequestError(source, "request_error", str(exc))


def record_source_error(errors, source, operation, exc, **context):
    entry = {
        "source": getattr(exc, "source", source),
        "operation": operation,
        "category": getattr(exc, "category", "unexpected_error"),
        "message": str(exc),
    }
    entry.update(context)
    errors.append(entry)


def source_error_retryable(exc):
    if exc.category in {"rate_limited", "network", "timeout"}:
        return True
    return exc.status is not None and 500 <= exc.status < 600

HARD_REJECT_PHRASES = (
    "free weekend", "weekend trial", "play for free", "free trial",
    "trial version", "demo", "playtest", "beta", "closed beta", "open beta",
)

COSMETIC_OR_PROMO_PHRASES = (
    "skin", "skins", "weapon skin", "weapon skins", "avatar", "wallpaper",
    "soundtrack", "artbook", "art book", "booster", "currency", "coins",
    "gems", "decal", "emote", "emoji", "profile background", "profile item",
    "camo", "camouflage", "spray", "helmet", "hat", "headgear", "dice pack",
    "dice set", "soldier items", "vehicle bundle", "welcome bundle",
    "certificate", "rare camo", "cosmetic pack", "cosmetics",
)

SERVICE_REWARD_PHRASES = (
    "starter kit", "starter pack", "beginner's starter kit", "beginners starter kit",
    "premium points", "premium currency", "premium account", "premium days",
    "7-day", "7 day", "30-day", "30 day", "temporary buff", "temporary buffs",
    "buff set", "buff sets", "emblem code", "emblem", "redeem code",
    "account reward", "in-game items", "boost your early progress",
    "credits", "gold", "tokens", "reward points", "arp required",
)

GAMEPLAY_DLC_HINTS = (
    "expansion", "story dlc", "campaign", "quest", "mission", "missions",
    "chapter", "scenario", "content pack", "new area", "new region",
    "new map", "new maps", "character pack", "class", "faction",
)

# These are not the taste profile itself. They map profile concepts to text signals
# commonly available from Steam/GamerPower. We only enable concept groups that
# actually exist in taste_profile.json.
CONCEPT_PATTERNS = {
    "story_choices_and_consequences": (
        "choices matter", "multiple endings", "choice", "choices", "consequence",
        "consequences", "branching", "interactive fiction",
    ),
    "exploration_and_lore": (
        "exploration", "explore", "open world", "lore", "discover", "discovery",
        "atmospheric", "story rich",
    ),
    "meaningful_system_progression": (
        "progression", "upgrade", "upgrades", "unlock", "unlocks", "research",
        "technology", "development",
    ),
    "automation_and_logistics": (
        "automation", "automate", "factory", "production", "logistics", "conveyor",
        "drone", "drones", "supply chain", "resource extraction",
    ),
    "city_building_and_colony_management": (
        "city builder", "city-building", "colony", "colony sim", "settlement",
        "town builder", "city-building", "management",
    ),
    "base_building": (
        "base building", "base-building", "build your base", "building",
    ),
    "survival_with_exploration": (
        "survival", "survival crafting", "open world survival",
    ),
    "collectibles_with_clear_tracking": (
        "collectibles", "collection", "collect", "completion",
    ),
    "multiple_meaningful_routes_or_endings": (
        "multiple endings", "branching narrative", "multiple routes",
    ),
    "stealth_with_multiple_approaches": (
        "stealth", "multiple approaches", "non-lethal", "nonlethal",
    ),
    "work_simulator_with_progression_and_finish": (
        "job simulator", "work simulator", "career", "business simulation",
    ),
    "achievements": (
        "steam achievements", "achievements",
    ),
    "crafting_that_unlocks_new_capabilities": (
        "crafting", "craft", "blueprints",
    ),
    "management_and_resource_systems": (
        "management", "resource management", "economy", "resources",
    ),
    "text_heavy_storytelling": (
        "visual novel", "text-based", "story rich", "narrative",
    ),
    "cozy_farming_with_development": (
        "farming sim", "farming", "life sim", "cozy",
    ),
    "pure_stat_growth": (
        "incremental", "stat grinding", "grind",
    ),
    "repetitive_grind": (
        "grind", "grinding", "endless grind",
    ),
    "long_runback_after_failure": (
        "souls-like", "soulslike",
    ),
    "arena_or_competitive_shooter": (
        "competitive", "pvp", "arena shooter", "hero shooter",
    ),
    "power_fantasy_shooter_without_other_hooks": (
        "boomer shooter", "arena fps",
    ),
    "looter_shooter": (
        "looter shooter", "loot shooter",
    ),
    "soulslike_pattern_memorization": (
        "souls-like", "soulslike",
    ),
    "instant_fail_stealth_as_whole_game": (
        "instant fail stealth",
    ),
    "hidden_collectibles_without_tracking": (
        "hidden collectibles",
    ),
    "endless_routine_without_new_progress": (
        "idle", "clicker", "incremental",
    ),
}

RATING_WEIGHTS = {
    "very_strong_positive": 5,
    "strong_positive": 3,
    "positive": 2,
    "conditional_positive": 1,
    "neutral": 0,
    "negative": -3,
    "strong_negative": -5,
}


def utc_now():
    return datetime.now(timezone.utc)


def utc_now_iso():
    return utc_now().replace(microsecond=0).isoformat()


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, payload):
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def snapshot_semantics(payload, history=False):
    semantic = {
        key: value
        for key, value in payload.items()
        if key != "updated_at_utc"
    }
    if history and isinstance(semantic.get("items"), dict):
        semantic["items"] = {
            key: {
                field: value
                for field, value in record.items()
                if field != "last_seen"
            }
            for key, record in semantic["items"].items()
        }
    return semantic


def heartbeat_due(previous, observed_at):
    try:
        previous_at = datetime.fromisoformat(previous["updated_at_utc"])
        current_at = datetime.fromisoformat(observed_at)
    except (KeyError, TypeError, ValueError):
        return True
    return (current_at - previous_at).total_seconds() >= SNAPSHOT_HEARTBEAT_SECONDS


def save_snapshot(path, payload, observed_at, history=False):
    try:
        previous = load_json(path, None)
    except (OSError, json.JSONDecodeError):
        previous = None

    should_write = (
        not isinstance(previous, dict)
        or snapshot_semantics(previous, history=history)
        != snapshot_semantics(payload, history=history)
        or heartbeat_due(previous, observed_at)
    )
    if should_write:
        save_json(path, payload)
    return should_write


def http_json(url, params=None, timeout=25, attempts=3):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
    )
    source = request_source(url)
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except SourceRequestError as exc:
            error = exc
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            error = classify_request_error(source, exc)

        if source_error_retryable(error) and attempt < attempts:
            time.sleep(3 * attempt)
            continue

        raise error


def clean_title(title):
    t = str(title).strip()
    t = re.sub(r"\s*Giveaway\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:Steam|PC)\)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\(Steam\)\s*Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*Steam\s+Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:Steam|PC)\)\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -")


def text_blob(item):
    return " ".join(
        str(item.get(k, "")) for k in
        ("title", "description", "instructions", "type", "platforms")
    ).lower()


def contains_phrase(blob, phrase):
    pattern = re.escape(str(phrase)).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", blob, flags=re.I) is not None


def parse_gamerpower_date(value):
    if not value or str(value).upper() == "N/A":
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def is_expired(item):
    end = parse_gamerpower_date(item.get("end_date"))
    return bool(end and end < utc_now())


def classify_delivery(item):
    blob = text_blob(item)
    instructions = str(item.get("instructions", "")).lower()

    if (
        "steam key" in blob
        or "activate a product on steam" in instructions
        or "unlock your key" in instructions
        or "claim a key" in instructions
    ):
        return "external_steam_key"

    if (
        "download this dlc directly via steam" in instructions
        or "download this pack directly via steam" in instructions
        or "download the dlc on steam" in instructions
    ):
        return "direct_steam_store"

    return "direct_or_unknown"


def classify_content(item):
    blob = text_blob(item)
    item_type = str(item.get("type", "")).lower()

    if item_type != "dlc":
        return "game_or_other"

    gameplay_hit = any(contains_phrase(blob, p) for p in GAMEPLAY_DLC_HINTS)
    strong_gameplay_hit = any(
        contains_phrase(blob, p)
        for p in GAMEPLAY_DLC_HINTS
        if p not in {"content pack"}
    )
    cosmetic_hit = any(contains_phrase(blob, p) for p in COSMETIC_OR_PROMO_PHRASES)
    service_hit = any(contains_phrase(blob, p) for p in SERVICE_REWARD_PHRASES)

    if service_hit and not strong_gameplay_hit:
        return "service_or_account_reward"
    if cosmetic_hit and not strong_gameplay_hit:
        return "cosmetic_or_promo_dlc"
    if gameplay_hit:
        return "gameplay_dlc"
    return "unknown_dlc"


def is_obvious_reject(item):
    blob = text_blob(item)

    if is_expired(item):
        return True, "expired_by_local_clock"

    for phrase in HARD_REJECT_PHRASES:
        if phrase in blob:
            return True, f"hard_reject:{phrase}"

    if str(item.get("type", "")).lower() == "beta":
        return True, "hard_reject:beta"

    kind = classify_content(item)

    if kind == "service_or_account_reward":
        return True, "service_or_account_reward"

    # Permanent cosmetic/music/artbook DLC may still be worth claiming when the
    # base game is owned. It is filtered later by the DLC ownership chain.
    return False, None


def normalize_gamerpower(item):
    return {
        "source": "GamerPower",
        "source_id": item.get("id"),
        "title": clean_title(item.get("title", "")),
        "source_title": item.get("title"),
        "type": str(item.get("type", "")),
        "platforms": item.get("platforms"),
        "description": item.get("description"),
        "instructions": item.get("instructions"),
        "worth": item.get("worth"),
        "published_date": item.get("published_date"),
        "end_date": item.get("end_date"),
        "status": item.get("status"),
        "delivery": classify_delivery(item),
        "content_kind": classify_content(item),
        "gamerpower_url": item.get("gamerpower_url")
            or item.get("open_giveaway_url")
            or item.get("open_giveaway"),
    }


def extract_owned_games(library):
    appids, names = set(), set()
    for game in library.get("games", []):
        try:
            appids.add(int(game["appid"]))
        except Exception:
            pass
        name = str(game.get("name", "")).strip().casefold()
        if name:
            names.add(name)
    return appids, names


def collect_owned_dlc_appids(data):
    found = set()

    def walk(value):
        if isinstance(value, dict):
            owned = value.get("owned") is True or str(value.get("status", "")).lower() == "owned"
            if owned:
                for key in ("appid", "app_id", "dlc_appid", "id"):
                    if key in value:
                        try:
                            found.add(int(value[key]))
                            break
                        except Exception:
                            pass
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return found


def normalize_tokens(value):
    return set(re.findall(r"[a-z0-9]+", str(value).casefold()))


def title_similarity(a, b):
    ta = normalize_tokens(a)
    tb = normalize_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def comparable_game_title(value):
    tokens = normalize_tokens(value)
    return tokens - {"the", "tm", "steam", "dlc", "content", "pack", "bundle"}


def same_game_title(expected, actual):
    expected_tokens = comparable_game_title(expected)
    actual_tokens = comparable_game_title(actual)
    if not expected_tokens or not actual_tokens:
        return False
    return len(expected_tokens & actual_tokens) / len(expected_tokens | actual_tokens) >= 0.75


def steam_search(title):
    payload = http_json(STEAM_SEARCH_URL, {"term": title, "l": "english", "cc": "RU"})
    return payload.get("items", []) if isinstance(payload, dict) else []


def title_variants(title):
    variants = [title]

    stripped = re.sub(r"\s+DLC\s*$", "", title, flags=re.I).strip()
    if stripped and stripped not in variants:
        variants.append(stripped)

    no_pack_word = re.sub(r"\s+Content\s+Pack\s*$", "", title, flags=re.I).strip()
    if no_pack_word and no_pack_word not in variants:
        variants.append(no_pack_word)

    punctuation_soft = re.sub(r"\s*[-:]\s*", " ", title)
    punctuation_soft = re.sub(r"\s+", " ", punctuation_soft).strip()
    if punctuation_soft and punctuation_soft not in variants:
        variants.append(punctuation_soft)

    return variants


def best_steam_match_for_queries(queries):
    candidates = []

    for query in queries:
        items = steam_search(query)

        for item in items[:10]:
            name = str(item.get("name", ""))
            score = max(title_similarity(q, name) for q in queries)
            candidates.append((score, query, item))

        time.sleep(REQUEST_DELAY_SECONDS)

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    score, query, item = candidates[0]

    if score < 0.45:
        return None

    return {
        "appid": item.get("id"),
        "name": item.get("name"),
        "search_match_score": round(score, 3),
        "query_used": query,
    }


def base_game_title_from_dlc(title):
    if " - " in title:
        return title.split(" - ", 1)[0].strip()
    if ":" in title:
        return title.split(":", 1)[0].strip()
    return None


def candidate_base_game_title(candidate):
    description = str(candidate.get("description", ""))
    explicit = re.search(
        r"base game\s+[\"']?(.+?)[\"']?(?:\s+on\s+steam)?\s+is\s+required",
        description,
        flags=re.I,
    )
    if explicit:
        return explicit.group(1).strip(" .,:;\"'")

    title = str(candidate.get("title", "")).strip()
    delimited = base_game_title_from_dlc(title)
    if delimited:
        return delimited

    descriptor_suffix = re.sub(
        r"\s+(?:alienware\s+)?(?:decal|skins?|weapons?\s+skins?|helmet|emblem\s+code|free\s+points)"
        r"(?:\s+(?:dlc|pack|bundle))?\s*$",
        "",
        title,
        flags=re.I,
    ).strip()
    if descriptor_suffix and descriptor_suffix.casefold() != title.casefold():
        return descriptor_suffix

    return None


def dlc_parent_matches_candidate(
    candidate, metadata, fallback_base_name=None, expected_base_appid=None
):
    fullgame = (metadata or {}).get("fullgame") or {}
    actual_parent = str(fullgame.get("name") or "").strip()
    expected_parent = candidate_base_game_title(candidate) or fallback_base_name
    if not expected_parent or not actual_parent:
        return False
    if expected_base_appid is not None:
        try:
            if int(fullgame.get("appid")) != int(expected_base_appid):
                return False
        except (TypeError, ValueError):
            return False
    return same_game_title(expected_parent, actual_parent)


def resolve_steam_match(candidate):
    title = candidate["title"]
    direct_queries = title_variants(title)

    direct = best_steam_match_for_queries(direct_queries)
    if direct:
        direct["lookup_mode"] = "candidate_title_variants"
        return direct

    if str(candidate.get("type", "")).lower() == "dlc":
        base = base_game_title_from_dlc(title)
        if base and base.casefold() != title.casefold():
            fallback = best_steam_match_for_queries([base])
            if fallback:
                fallback["lookup_mode"] = "base_game_fallback"
                fallback["base_game_query"] = base
                return fallback

    return None


def inspect_steam_ru(appid):
    # English metadata for stable scoring; cc=RU preserves RU storefront price/availability.
    payload = http_json(
        STEAM_APPDETAILS_URL,
        {"appids": appid, "cc": "RU", "l": "english"},
    )
    wrapper = payload.get(str(appid)) if isinstance(payload, dict) else None

    if not wrapper or not wrapper.get("success"):
        return {
            "checked": True,
            "reachable": False,
            "verification": "unknown",
            "reason": "appdetails_unavailable_for_ru",
        }

    data = wrapper.get("data") or {}
    app_type = str(data.get("type", "")).lower()
    is_free = bool(data.get("is_free"))
    price = data.get("price_overview") or {}
    initial = price.get("initial")
    final = price.get("final")
    discount = price.get("discount_percent")
    final_formatted = str(price.get("final_formatted", "")).strip().casefold()

    direct_keep_forever = (
        app_type in {"game", "dlc"}
        and isinstance(initial, int)
        and isinstance(final, int)
        and initial > 0
        and (final == 0 or final_formatted == "free")
        and discount == 100
    )

    if direct_keep_forever:
        verification = "strong_keep_forever_candidate"
        reason = "paid_app_at_100_percent_discount_in_ru"
    elif is_free:
        verification = "reject_f2p"
        reason = "steam_marks_app_as_permanently_free"
    elif app_type in {"demo", "beta", "mod"}:
        verification = "reject_non_full_product"
        reason = f"steam_type:{app_type}"
    elif price:
        verification = "not_free_in_ru"
        reason = f"ru_final_price:{final}"
    else:
        verification = "needs_review"
        reason = "no_ru_price_overview_or_nonstandard_offer"

    return {
        "appid": int(appid),
        "checked": True,
        "reachable": True,
        "verification": verification,
        "reason": reason,
        "steam_type": app_type,
        "steam_name": data.get("name"),
        "is_free": is_free,
        "price_overview": price,
        "developers": data.get("developers", []),
        "publishers": data.get("publishers", []),
        "genres": [g.get("description") for g in data.get("genres", []) if isinstance(g, dict)],
        "categories": [c.get("description") for c in data.get("categories", []) if isinstance(c, dict)],
        "short_description": data.get("short_description"),
        "dlc": data.get("dlc", []),
        "fullgame": data.get("fullgame"),
    }



def resolve_dlc_from_base_catalog(
    candidate, base_metadata, max_checks=40, source_errors=None
):
    """Try to identify a DLC app from the base game's Steam DLC list."""
    dlc_ids = list((base_metadata or {}).get("dlc") or [])[:max_checks]
    if not dlc_ids:
        return None, None

    queries = title_variants(candidate.get("title", ""))
    expected_base_name = (
        candidate_base_game_title(candidate)
        or str((base_metadata or {}).get("steam_name") or "").strip()
    )
    expected_base_appid = (base_metadata or {}).get("appid")
    best = None

    for dlc_id in dlc_ids:
        try:
            time.sleep(REQUEST_DELAY_SECONDS)
            meta = inspect_steam_ru(int(dlc_id))
        except Exception as exc:
            if source_errors is not None:
                record_source_error(
                    source_errors,
                    "steam",
                    "dlc_appdetails",
                    exc,
                    appid=int(dlc_id),
                )
            continue

        if meta.get("steam_type") != "dlc":
            continue

        if not dlc_parent_matches_candidate(
            candidate, meta, expected_base_name, expected_base_appid
        ):
            continue

        name = str(meta.get("steam_name") or "")
        score = max((title_similarity(q, name) for q in queries), default=0.0)

        if best is None or score > best[0]:
            best = (score, int(dlc_id), name, meta)

        if score >= 0.92:
            break

    if not best or best[0] < 0.45:
        return None, None

    score, appid, name, meta = best
    return {
        "appid": appid,
        "name": name,
        "search_match_score": round(score, 3),
        "lookup_mode": "base_game_dlc_catalog",
        "matched_product_role": "candidate_product",
    }, meta


def apply_dlc_ownership_gate(candidate, owned_game_appids):
    """Return True only when the DLC base is resolved and owned."""
    if str(candidate.get("type", "")).lower() != "dlc":
        return True

    base = candidate.get("base_game") or {}
    if base.get("appid"):
        expected_base = candidate_base_game_title(candidate)
        if expected_base and not same_game_title(expected_base, base.get("name")):
            candidate["dlc_gate"] = {
                "eligible": None,
                "reason": "base_game_name_mismatch",
                "expected_base_name": expected_base,
                "resolved_base_name": base.get("name"),
            }
            return False

        product_meta = candidate.get("steam_ru") or {}
        product_fullgame = product_meta.get("fullgame") or {}
        if product_meta.get("steam_type") == "dlc" and product_fullgame.get("appid"):
            try:
                parent_appid_matches = int(product_fullgame["appid"]) == int(base["appid"])
            except (TypeError, ValueError):
                parent_appid_matches = False
            if not parent_appid_matches:
                candidate["dlc_gate"] = {
                    "eligible": None,
                    "reason": "base_game_appid_mismatch",
                    "expected_base_appid": int(base["appid"]),
                    "resolved_base_appid": product_fullgame.get("appid"),
                    "expected_base_name": base.get("name"),
                    "resolved_base_name": product_fullgame.get("name"),
                }
                return False

        owned = int(base["appid"]) in owned_game_appids
        base["owned"] = owned
        candidate["base_game"] = base
        if not owned:
            candidate["dlc_gate"] = {
                "eligible": False,
                "reason": "base_game_not_owned",
                "base_appid": int(base["appid"]),
            }
            return False

        match = candidate.get("steam_match") or {}
        product_is_resolved = (
            match.get("matched_product_role") == "candidate_product"
            and product_meta.get("steam_type") == "dlc"
            and bool(match.get("appid"))
        )
        if not product_is_resolved:
            candidate["dlc_gate"] = {
                "eligible": None,
                "reason": "dlc_product_unresolved",
                "base_appid": int(base["appid"]),
            }
            return False

        candidate["dlc_gate"] = {
            "eligible": True,
            "reason": "base_game_owned",
            "base_appid": int(base["appid"]),
        }
        return True

    steam = candidate.get("steam_ru") or {}
    fullgame = steam.get("fullgame") or {}
    try:
        base_appid = int(fullgame.get("appid"))
    except Exception:
        base_appid = None

    if base_appid:
        expected_base = candidate_base_game_title(candidate)
        resolved_base = fullgame.get("name")
        if expected_base and not same_game_title(expected_base, resolved_base):
            candidate["dlc_gate"] = {
                "eligible": None,
                "reason": "base_game_name_mismatch",
                "expected_base_name": expected_base,
                "resolved_base_name": resolved_base,
                "resolved_base_appid": base_appid,
            }
            return False

        owned = base_appid in owned_game_appids
        candidate["base_game"] = {
            "appid": base_appid,
            "name": fullgame.get("name"),
            "owned": owned,
        }
        if not owned:
            candidate["dlc_gate"] = {
                "eligible": False,
                "reason": "base_game_not_owned",
                "base_appid": base_appid,
            }
            return False

        match = candidate.get("steam_match") or {}
        product_is_resolved = (
            match.get("matched_product_role") == "candidate_product"
            and steam.get("steam_type") == "dlc"
            and bool(match.get("appid"))
        )
        if not product_is_resolved:
            candidate["dlc_gate"] = {
                "eligible": None,
                "reason": "dlc_product_unresolved",
                "base_appid": base_appid,
            }
            return False

        candidate["dlc_gate"] = {
            "eligible": True,
            "reason": "base_game_owned",
            "base_appid": base_appid,
        }
        return True

    candidate["dlc_gate"] = {
        "eligible": None,
        "reason": "base_game_unresolved",
    }
    return False

def key_region_status(candidate):
    blob = text_blob(candidate)

    ru_block_phrases = (
        "not available in russia",
        "not available in russian federation",
        "russia excluded",
        "excluding russia",
        "except russia",
        "ru excluded",
        "region locked: ru",
    )

    global_phrases = (
        "worldwide",
        "global key",
        "global steam key",
        "available worldwide",
    )

    if any(p in blob for p in ru_block_phrases):
        return "ru_blocked_explicit"

    if any(p in blob for p in global_phrases):
        return "global_claim_text_seen"

    return "unknown_no_explicit_ru_block_seen"


def profile_enabled_signals(taste):
    signals = taste.get("signals") or {}
    enabled = {}

    for bucket, names in signals.items():
        weight = RATING_WEIGHTS.get(bucket, 0)
        if isinstance(names, list):
            for name in names:
                enabled[str(name)] = weight

    return enabled


def genre_preference_signals(taste):
    prefs = taste.get("genre_preferences") or {}
    result = {}

    # Genre-profile keys are mapped to common Steam wording.
    aliases = {
        "narrative_choices": ("choices matter", "story rich", "interactive fiction", "visual novel"),
        "dark_and_morally_heavy": ("dark", "psychological", "dystopian"),
        "survival": ("survival",),
        "base_building": ("base building", "building"),
        "crafting": ("crafting",),
        "sandbox": ("sandbox", "open world"),
        "automation_and_production": ("automation", "factory", "production"),
        "work_simulators": ("job simulator", "simulation"),
        "management": ("management",),
        "city_building_and_colonies": ("city builder", "colony sim", "city-building"),
        "detective_and_deduction": ("detective", "investigation", "mystery"),
        "shooters": ("shooter", "fps", "third-person shooter"),
        "soulslike": ("souls-like", "soulslike"),
        "stealth": ("stealth",),
        "roguelike_and_roguelite": ("roguelike", "roguelite"),
        "visual_novels_and_text_games": ("visual novel", "text-based"),
        "cozy_farming_and_daily_life": ("farming sim", "life sim", "cozy"),
        "metroidvania": ("metroidvania",),
        "co_op": ("co-op", "cooperative"),
    }

    for pref_name, pref in prefs.items():
        if not isinstance(pref, dict):
            continue
        rating = str(pref.get("rating", "neutral"))
        weight = RATING_WEIGHTS.get(rating, 0)
        for alias in aliases.get(pref_name, ()):
            result[alias] = (weight, pref_name)

    return result


def taste_score(candidate, taste):
    if str(candidate.get("type", "")).lower() == "dlc":
        dlc_gate = candidate.get("dlc_gate") or {}
        if dlc_gate.get("eligible") is not True:
            return 0, [], [], []

    steam = candidate.get("steam_ru") or {}
    blob = " ".join([
        str(candidate.get("title", "")),
        str(candidate.get("description", "")),
        str(steam.get("short_description", "")),
        " ".join(steam.get("genres", []) or []),
        " ".join(steam.get("categories", []) or []),
    ]).lower()

    score = 0
    positives = []
    negatives = []
    matched_concepts = []

    enabled_signals = profile_enabled_signals(taste)

    for concept, concept_weight in enabled_signals.items():
        if (
            candidate.get("content_kind") == "cosmetic_or_promo_dlc"
            and concept == "meaningful_system_progression"
        ):
            continue

        patterns = CONCEPT_PATTERNS.get(concept, ())
        hits = [p for p in patterns if p in blob]
        if not hits:
            continue

        # One concept scores once even if several synonyms appear.
        score += concept_weight
        matched_concepts.append({
            "concept": concept,
            "weight": concept_weight,
            "evidence": hits[:3],
        })

        if concept_weight > 0:
            positives.append(concept)
        elif concept_weight < 0:
            negatives.append(concept)

    for alias, (weight, pref_name) in genre_preference_signals(taste).items():
        if alias not in blob or weight == 0:
            continue

        # Genre preference is a modifier; weaker than primary concept signal.
        modifier = 1 if weight > 0 else -2
        if abs(weight) >= 3:
            modifier = 2 if weight > 0 else -3
        if abs(weight) >= 5:
            modifier = 3 if weight > 0 else -4

        score += modifier
        marker = f"genre:{pref_name}"

        if modifier > 0 and marker not in positives:
            positives.append(marker)
        if modifier < 0 and marker not in negatives:
            negatives.append(marker)

    # Achievements are already represented by the profile concept above.
    # Do not count the same Steam category twice.

    # A combat loop by itself is a weak hook for this profile. Penalize only
    # when combat language is strong AND no primary story/world/exploration/
    # system hook is visible in the metadata.
    combat_terms = (
        "massive hordes", "hordes of", "slay them all", "mob density",
        "action-packed arpg", "hack and slash", "hack-and-slash",
        "arena combat", "endless combat",
    )
    primary_hook_concepts = {
        "story_choices_and_consequences",
        "exploration_and_lore",
        "meaningful_system_progression",
        "automation_and_logistics",
        "city_building_and_colony_management",
        "base_building",
        "survival_with_exploration",
        "text_heavy_storytelling",
    }
    combat_hits = [term for term in combat_terms if term in blob]
    has_primary_hook = any(
        m.get("concept") in primary_hook_concepts and m.get("weight", 0) > 0
        for m in matched_concepts
    )
    if len(combat_hits) >= 2 and not has_primary_hook:
        score -= 4
        negatives.append("combat_loop_without_primary_hook")
        matched_concepts.append({
            "concept": "combat_loop_without_primary_hook",
            "weight": -4,
            "evidence": combat_hits[:3],
        })

    return score, positives, negatives, matched_concepts


def explain_match(candidate, score, positives, negatives):
    bits = []

    dlc_gate = candidate.get("dlc_gate") or {}
    if dlc_gate.get("eligible") is False:
        bits.append("base game is not owned; DLC has no current use")
    elif dlc_gate.get("reason") == "dlc_product_unresolved":
        bits.append("base game is owned but the exact DLC product is unresolved; taste scoring deferred")
    elif (
        str(candidate.get("type", "")).lower() == "dlc"
        and dlc_gate.get("eligible") is not True
    ):
        bits.append("base game unresolved or mismatched; taste scoring deferred")

    if candidate.get("content_kind") == "cosmetic_or_promo_dlc":
        bits.append("permanent bonus/cosmetic DLC; surface only when base game is owned")

    if candidate.get("delivery") == "external_steam_key":
        bits.append("external Steam key; store price does not invalidate the giveaway")

    if candidate.get("content_kind") == "gameplay_dlc":
        bits.append("gameplay DLC rather than promo/account reward")

    if positives:
        bits.append("positive fit: " + ", ".join(positives[:4]))

    if negatives:
        bits.append("negative fit: " + ", ".join(negatives[:3]))

    if not positives and not negatives:
        bits.append("Steam/GamerPower metadata does not expose enough taste signals yet")

    return "; ".join(bits)


def recommendation_band(candidate, score):
    delivery = candidate.get("delivery")
    verification = (candidate.get("steam_ru") or {}).get("verification")
    key_region = candidate.get("key_region_status")

    dlc_gate = candidate.get("dlc_gate") or {}
    if dlc_gate.get("eligible") is False:
        return "skip"

    if delivery == "external_steam_key" and key_region == "ru_blocked_explicit":
        return "skip"

    if (
        str(candidate.get("type", "")).lower() == "dlc"
        and dlc_gate.get("eligible") is not True
    ):
        return "needs_review"

    if delivery == "external_steam_key":
        if verification == "reject_non_full_product":
            return "skip"
        if score >= 6:
            return "likely_match"
        if score >= 2:
            return "conditional"
        if score <= -4:
            return "low_priority"
        return "needs_review"

    if verification in {"reject_f2p", "reject_non_full_product", "not_free_in_ru"}:
        return "skip"

    if verification == "strong_keep_forever_candidate":
        if score >= 6:
            return "must_claim"
        if score >= 2:
            return "likely_match"
        if score <= -4:
            return "low_priority"
        return "conditional"

    return "needs_review"


def update_history_record(
    history,
    candidate,
    now,
    band,
    score,
    verification,
    filter_reason=None,
    ownership_reason=None,
):
    key = f"gamerpower:{candidate.get('source_id')}"
    old = history["items"].get(key, {})
    record = {
        "title": candidate["title"],
        "first_seen": old.get("first_seen", now),
        "last_seen": now,
        "last_band": band,
        "last_score": score,
        "last_verification": verification,
        "last_end_date": candidate.get("end_date"),
        "delivery": candidate.get("delivery"),
        "key_region_status": candidate.get("key_region_status"),
        "content_kind": candidate.get("content_kind"),
    }
    if filter_reason is not None:
        record["last_filter_reason"] = filter_reason
    if ownership_reason is not None:
        record["last_ownership_reason"] = ownership_reason
    history["items"][key] = record


def main():
    library = load_json(LIBRARY_FILE, {})
    owned_dlc_data = load_json(OWNED_DLC_FILE, {})
    taste = load_json(TASTE_FILE, {})

    owned_game_appids, owned_game_names = extract_owned_games(library)
    owned_dlc_appids = collect_owned_dlc_appids(owned_dlc_data)

    source_errors = []
    raw = http_json(GAMERPOWER_URL)
    if not isinstance(raw, list):
        raise SourceRequestError(
            "gamerpower",
            "malformed_response",
            "GamerPower returned malformed response: "
            f"expected a list, got {type(raw).__name__}.",
        )
    if not all(isinstance(item, dict) for item in raw):
        raise SourceRequestError(
            "gamerpower",
            "malformed_response",
            "GamerPower returned malformed response: "
            "expected every item to be an object.",
        )

    history = load_json(HISTORY_FILE, {"schema_version": 1, "items": {}})
    history.setdefault("schema_version", 1)
    history.setdefault("items", {})

    normalized, matches = [], []
    now = utc_now_iso()

    for source_item in raw:
        candidate = normalize_gamerpower(source_item)

        candidate["key_region_status"] = (
            key_region_status(source_item)
            if candidate["delivery"] == "external_steam_key"
            else None
        )

        rejected, reason = is_obvious_reject(source_item)
        candidate["pre_filter"] = {"rejected": rejected, "reason": reason}
        normalized.append(candidate)

        if rejected:
            update_history_record(
                history,
                candidate,
                now,
                "skip",
                0,
                "pre_filter_rejected",
                filter_reason=reason,
            )
            continue

        if candidate["title"].casefold() in owned_game_names:
            candidate["ownership"] = {
                "owned": True,
                "reason": "library_title_match",
            }
            update_history_record(
                history,
                candidate,
                now,
                "skip",
                0,
                "already_owned",
                ownership_reason="library_title_match",
            )
            continue

        try:
            match = resolve_steam_match(candidate)
        except SourceRequestError as exc:
            error_context = {
                "source_id": candidate.get("source_id"),
                "title": candidate.get("title"),
            }
            if exc.status is not None:
                error_context["status"] = exc.status
            record_source_error(
                source_errors,
                "steam",
                "search",
                exc,
                **error_context,
            )
            match = None
            candidate["steam_ru"] = {
                "checked": False,
                "verification": "unknown",
                "reason": f"steam_search_request_error:{exc.category}",
            }
        candidate["steam_match"] = match

        if not match or not match.get("appid"):
            if not candidate.get("steam_ru"):
                candidate["steam_ru"] = {
                    "checked": False,
                    "verification": "needs_review",
                    "reason": "no_confident_steam_appid_match",
                }

        else:
            appid = int(match["appid"])

            time.sleep(REQUEST_DELAY_SECONDS)
            try:
                inspected = inspect_steam_ru(appid)
            except Exception as exc:
                record_source_error(
                    source_errors,
                    "steam",
                    "appdetails",
                    exc,
                    appid=appid,
                )
                inspected = {
                    "checked": True,
                    "reachable": False,
                    "verification": "unknown",
                    "reason": (
                        "steam_request_error:"
                        f"{getattr(exc, 'category', type(exc).__name__)}"
                    ),
                }

            steam_type = inspected.get("steam_type")
            source_type = str(candidate.get("type", "")).lower()

            # v0.5: Steam's actual app type decides the product role.
            if source_type == "dlc" and steam_type == "game":
                candidate["base_game"] = {
                    "appid": appid,
                    "name": inspected.get("steam_name") or match.get("name"),
                    "owned": appid in owned_game_appids,
                }
                candidate["steam_ru"] = {
                    "checked": False,
                    "verification": "needs_review",
                    "reason": "base_game_found_but_dlc_appid_unresolved",
                    "base_game_store_metadata": inspected,
                }
                match["matched_product_role"] = "base_game_only"

            elif (
                source_type == "dlc"
                and steam_type == "dlc"
                and not dlc_parent_matches_candidate(candidate, inspected)
            ):
                match["matched_product_role"] = "rejected_parent_mismatch"
                candidate["rejected_steam_match"] = match
                candidate["steam_match"] = None
                candidate["steam_ru"] = {
                    "checked": False,
                    "verification": "needs_review",
                    "reason": "base_game_name_mismatch",
                    "rejected_product_metadata": inspected,
                }
                candidate["dlc_gate"] = {
                    "eligible": None,
                    "reason": "base_game_name_mismatch",
                    "expected_base_name": candidate_base_game_title(candidate),
                    "resolved_base_name": ((inspected.get("fullgame") or {}).get("name")),
                    "resolved_base_appid": ((inspected.get("fullgame") or {}).get("appid")),
                }

            else:
                match["matched_product_role"] = "candidate_product"
                candidate["steam_ru"] = inspected

                if appid in owned_game_appids or appid in owned_dlc_appids:
                    candidate["ownership"] = {
                        "owned": True,
                        "reason": "appid_match",
                        "appid": appid,
                    }
                    update_history_record(
                        history,
                        candidate,
                        now,
                        "skip",
                        0,
                        "already_owned",
                        ownership_reason="appid_match",
                    )
                    continue

        # DLC usefulness gate: no owned base game -> no recommendation.
        # If the base IS owned but only the base app was resolved, use Steam's
        # DLC catalog to try to resolve the actual DLC before scoring.
        if str(candidate.get("type", "")).lower() == "dlc":
            base = candidate.get("base_game") or {}
            base_meta = ((candidate.get("steam_ru") or {}).get("base_game_store_metadata") or {})

            if base.get("appid") and int(base["appid"]) in owned_game_appids:
                dlc_match, dlc_meta = resolve_dlc_from_base_catalog(
                    candidate, base_meta, source_errors=source_errors
                )
                if dlc_match and dlc_meta:
                    candidate["steam_match"] = dlc_match
                    candidate["steam_ru"] = dlc_meta

                    resolved_dlc_appid = int(dlc_match["appid"])
                    if resolved_dlc_appid in owned_dlc_appids:
                        candidate["ownership"] = {
                            "owned": True,
                            "reason": "appid_match",
                            "appid": resolved_dlc_appid,
                        }
                        update_history_record(
                            history,
                            candidate,
                            now,
                            "skip",
                            0,
                            "already_owned",
                            ownership_reason="appid_match",
                        )
                        continue

            if not candidate.get("dlc_gate"):
                apply_dlc_ownership_gate(candidate, owned_game_appids)

        score, positives, negatives, concepts = taste_score(candidate, taste)
        band = recommendation_band(candidate, score)

        candidate["taste"] = {
            "score": score,
            "positive_signals": positives,
            "negative_signals": negatives,
            "matched_concepts": concepts,
            "band": band,
            "explanation": explain_match(candidate, score, positives, negatives),
        }

        update_history_record(
            history,
            candidate,
            now,
            band,
            score,
            (candidate.get("steam_ru") or {}).get("verification"),
        )

        matches.append(candidate)

    band_order = {
        "must_claim": 0,
        "likely_match": 1,
        "conditional": 2,
        "family_only": 3,
        "needs_review": 4,
        "low_priority": 5,
        "skip": 6,
    }

    matches.sort(
        key=lambda item: (
            band_order.get((item.get("taste") or {}).get("band"), 99),
            -int((item.get("taste") or {}).get("score", 0)),
            str(item.get("end_date") or "9999"),
        )
    )

    giveaways_payload = {
        "schema_version": "0.7.0",
        "source": "GamerPower",
        "source_attribution": "Data discovery by GamerPower.com",
        "updated_at_utc": now,
        "run_degraded": bool(source_errors),
        "source_errors": source_errors,
        "candidate_count": len(normalized),
        "items": normalized,
    }

    matches_payload = {
        "schema_version": "0.7.0",
        "updated_at_utc": now,
        "run_degraded": bool(source_errors),
        "source_errors": source_errors,
        "region": (taste.get("hard_filters") or {}).get("region", "RU"),
        "match_count": len(matches),
        "bands": {
            band: sum(
                1 for item in matches
                if (item.get("taste") or {}).get("band") == band
            )
            for band in band_order
        },
        "items": matches,
    }

    history["schema_version"] = "0.7.0"
    history["updated_at_utc"] = now
    save_snapshot(GIVEAWAYS_FILE, giveaways_payload, now)
    save_snapshot(MATCHES_FILE, matches_payload, now)
    save_snapshot(HISTORY_FILE, history, now, history=True)

    print(f"GamerPower candidates: {len(normalized)}")
    print(f"Filtered candidates: {len(matches)}")

    for item in matches[:20]:
        taste_info = item.get("taste") or {}
        verification = (item.get("steam_ru") or {}).get("verification")
        print(
            f"- [{taste_info.get('band')}] score={taste_info.get('score')} "
            f"[{item.get('content_kind')}] [{item.get('delivery')}] "
            f"[{verification}] {item.get('title')}"
        )


if __name__ == "__main__":
    main()
