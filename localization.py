from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CATEGORY_ORDER = ("teams", "national_teams", "players", "leagues", "sports", "venues")


def has_hangul(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value or ""))


def normalize(value: str) -> str:
    return re.sub(r"[\s\-_.,:()'\"/]+", "", str(value or "").casefold())


def load_terms(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def korean_name(value: str, terms: dict[str, Any], category: str | None = None) -> str:
    if not value:
        return ""
    categories = [category] if category else CATEGORY_ORDER
    for item in categories:
        mapping = terms.get(item, {})
        if isinstance(mapping, dict) and value in mapping:
            return str(mapping[value])
    return value


def display_term(value: str, terms: dict[str, Any], category: str | None = None) -> str:
    if not value:
        return ""
    korean = korean_name(value, terms, category)
    if korean == value:
        return value
    if has_hangul(value):
        return korean
    return f"{korean} ({value})"


def canonical_term(value: str, terms: dict[str, Any], category: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    aliases = terms.get("aliases", {}).get(category, {})
    if isinstance(aliases, dict):
        normalized_raw = normalize(raw)
        for original, values in aliases.items():
            for alias in values:
                if normalized_raw == normalize(str(alias)):
                    return str(original)

    category_map = terms.get(category, {})
    if isinstance(category_map, dict):
        if raw in category_map:
            return raw
        normalized_raw = normalize(raw)
        for original, korean in category_map.items():
            candidates = {
                normalize(str(original)),
                normalize(str(korean)),
                normalize(display_term(str(original), terms, category)),
            }
            if normalized_raw in candidates:
                return str(original)

    return raw


def canonical_terms(values: list[str], terms: dict[str, Any], category: str) -> list[str]:
    result = []
    seen = set()
    for value in values:
        canonical = canonical_term(value, terms, category)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result


def display_event_title(event: dict[str, Any], terms: dict[str, Any]) -> str:
    home = display_term(str(event.get("home_team") or "TBD"), terms, "teams")
    away = display_term(str(event.get("away_team") or "TBD"), terms, "teams")
    league = display_term(str(event.get("league") or event.get("sport") or "Sports"), terms, "leagues")
    return f"{home} vs {away} - {league}"


def display_event_meta(event: dict[str, Any], terms: dict[str, Any]) -> str:
    sport = display_term(str(event.get("sport", "-")), terms, "sports")
    league = display_term(str(event.get("league", "-")), terms, "leagues")
    venue = display_term(str(event.get("venue", "-")), terms, "venues")
    return f"{sport} | {league} | {event.get('display_time', '-')} | {venue}"
