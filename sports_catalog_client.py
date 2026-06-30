from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from localization import canonical_term, display_term
from matchmate_agent import load_json, save_json
from runtime_config import config_value


DEFAULT_CATALOG_CACHE = {
    "teams": [],
    "players": [],
    "national_teams": [],
    "records": [],
}


@dataclass
class CatalogSearchResult:
    category: str
    query: str
    names: list[str]
    records: list[dict[str, Any]]
    source: str
    warning: str = ""


class SportsCatalogClient:
    def __init__(self, cache_path: Path, timeout: int = 12):
        self.cache_path = cache_path
        self.timeout = timeout
        api_key = config_value("THESPORTSDB_API_KEY", "123")
        self.base_url = f"https://www.thesportsdb.com/api/v1/json/{api_key}"

    def load_cache(self) -> dict[str, Any]:
        data = load_json(self.cache_path, DEFAULT_CATALOG_CACHE)
        for key in ("teams", "players", "national_teams", "records"):
            data.setdefault(key, [])
        return data

    def save_cache(self, data: dict[str, Any]) -> None:
        for key in ("teams", "players", "national_teams"):
            data[key] = sorted(set(str(item) for item in data.get(key, []) if item))
        records = []
        record_positions = {}
        for record in data.get("records", []):
            key = (record.get("category"), record.get("id"), record.get("name"))
            if key in record_positions:
                current = records[record_positions[key]]
                current_has_image = bool(current.get("thumb") or current.get("badge"))
                record_has_image = bool(record.get("thumb") or record.get("badge"))
                if record_has_image and not current_has_image:
                    records[record_positions[key]] = record
                continue
            record_positions[key] = len(records)
            records.append(record)
        data["records"] = records
        save_json(self.cache_path, data)

    def search(self, category: str, query: str, terms: dict[str, Any]) -> CatalogSearchResult:
        clean_query = str(query or "").strip()
        if not clean_query:
            return CatalogSearchResult(category, query, [], [], "thesportsdb", "검색어가 비어 있습니다.")

        if category in {"teams", "national_teams"}:
            return self._search_teams(category, clean_query, terms)
        if category == "players":
            return self._search_players(clean_query, terms)
        return CatalogSearchResult(category, query, [], [], "thesportsdb", f"지원하지 않는 카테고리입니다: {category}")

    def search_and_cache(self, category: str, query: str, terms: dict[str, Any]) -> CatalogSearchResult:
        result = self.search(category, query, terms)
        if result.names:
            cache = self.load_cache()
            cache.setdefault(category, [])
            cache[category].extend(result.names)
            cache.setdefault("records", [])
            cache["records"].extend(result.records)
            self.save_cache(cache)
        return result

    def resolve_values(self, category: str, values: list[str], terms: dict[str, Any]) -> tuple[list[str], list[str]]:
        resolved = []
        notes = []
        cache = self.load_cache()
        known = set(cache.get(category, []))

        for value in values:
            canonical = canonical_term(value, terms, category)
            if not canonical:
                continue
            if canonical in known or canonical != value:
                resolved.append(canonical)
                continue

            result = self.search_and_cache(category, canonical, terms)
            if result.names:
                resolved.append(result.names[0])
                if result.names[0] != canonical:
                    notes.append(f"{display_term(canonical, terms, category)} -> {display_term(result.names[0], terms, category)}")
            else:
                resolved.append(canonical)
                if result.warning:
                    notes.append(f"{canonical}: {result.warning}")

        deduped = []
        seen = set()
        for item in resolved:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped, notes

    def find_team_logo_record(self, category: str, team_name: str, terms: dict[str, Any]) -> dict[str, Any]:
        result = self.search_and_cache(category, team_name, terms)
        fallback = result.records[0] if result.records else {"category": category, "name": team_name}
        for record in result.records:
            if record.get("badge") or record.get("thumb"):
                return record

        wiki_record = self._search_wikimedia_team_logo(category, team_name, terms)
        if wiki_record:
            cache = self.load_cache()
            cache.setdefault(category, []).append(str(wiki_record.get("name") or team_name))
            cache.setdefault("records", []).append(wiki_record)
            self.save_cache(cache)
            return wiki_record

        return fallback

    def _search_teams(self, category: str, query: str, terms: dict[str, Any]) -> CatalogSearchResult:
        canonical_query = canonical_term(query, terms, category)
        response = requests.get(
            f"{self.base_url}/searchteams.php",
            params={"t": canonical_query},
            timeout=self.timeout,
        )
        response.raise_for_status()
        teams = response.json().get("teams") or []
        records = []
        names = []
        for team in teams:
            name = team.get("strTeam")
            if not name:
                continue
            league = team.get("strLeague") or ""
            country = team.get("strCountry") or ""
            is_national = "World Cup" in league or name in {"South Korea", "Korea Republic", "Japan", "United States"}
            if category == "national_teams" and not is_national:
                continue
            record_category = "national_teams" if is_national else "teams"
            if category == "teams" and record_category == "national_teams":
                continue
            if name == "South Korea":
                name = "Korea Republic"
            names.append(name)
            records.append(
                {
                    "category": category,
                    "id": team.get("idTeam"),
                    "name": name,
                    "sport": team.get("strSport"),
                    "league": league,
                    "country": country,
                    "badge": team.get("strTeamBadge"),
                    "source": "thesportsdb",
                }
            )
        warning = "" if names else "TheSportsDB에서 일치하는 팀을 찾지 못했습니다."
        return CatalogSearchResult(category, query, names, records, "thesportsdb", warning)

    def _team_title_candidates(self, team_name: str, terms: dict[str, Any], category: str) -> list[str]:
        canonical = canonical_term(team_name, terms, category) or team_name
        candidates = [canonical]
        if category == "national_teams":
            national_title_base = {
                "Korea Republic": "South Korea",
                "대한민국": "South Korea",
                "United States": "United States",
            }.get(canonical, canonical)
            special_titles = {
                "United States": [
                    "United States men's national soccer team",
                    "United States national soccer team",
                ],
            }
            candidates = special_titles.get(national_title_base, [])
            candidates.extend(
                [
                    f"{national_title_base} national football team",
                    f"{national_title_base} men's national football team",
                    f"{national_title_base} national team",
                    national_title_base,
                    canonical,
                ]
            )
        elif category == "teams":
            club_titles = {
                "Arsenal": "Arsenal F.C.",
                "Bayern Munich": "FC Bayern Munich",
                "Chelsea": "Chelsea F.C.",
                "FC Barcelona": "FC Barcelona",
                "Liverpool": "Liverpool F.C.",
                "Manchester City": "Manchester City F.C.",
                "Manchester United": "Manchester United F.C.",
                "Paris Saint-Germain": "Paris Saint-Germain FC",
                "Real Madrid": "Real Madrid CF",
                "Tottenham Hotspur": "Tottenham Hotspur F.C.",
            }
            if canonical in club_titles:
                candidates = [club_titles[canonical], canonical]
            elif not canonical.endswith("F.C."):
                candidates.append(f"{canonical} F.C.")

        result = []
        seen = set()
        for candidate in candidates:
            clean = str(candidate or "").strip()
            if not clean or clean.casefold() in seen:
                continue
            seen.add(clean.casefold())
            result.append(clean)
        return result

    def _search_wikimedia_team_logo(self, category: str, team_name: str, terms: dict[str, Any]) -> dict[str, Any] | None:
        for title in self._team_title_candidates(team_name, terms, category):
            entity_id = self._wikidata_entity_id(title)
            logo_url = self._wikidata_logo_url(entity_id, category) if entity_id else ""
            if logo_url:
                return {
                    "category": category,
                    "id": f"wikidata:{entity_id}:{title}",
                    "name": canonical_term(team_name, terms, category) or team_name,
                    "badge": logo_url,
                    "source": "wikidata",
                    "image_kind": "national_team_mark" if category == "national_teams" else "club_logo",
                    "source_title": title,
                }

            thumbnail = self._wikipedia_page_thumbnail(title)
            if thumbnail:
                return {
                    "category": category,
                    "id": f"wikipedia:{title}",
                    "name": canonical_term(team_name, terms, category) or team_name,
                    "badge": thumbnail,
                    "source": "wikipedia",
                    "image_kind": "national_team_page" if category == "national_teams" else "club_logo",
                    "source_title": title,
                }
        return None

    def _wikipedia_page_thumbnail(self, title: str) -> str:
        try:
            response = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "titles": title,
                    "prop": "pageimages",
                    "format": "json",
                    "pithumbsize": 160,
                },
                headers={"User-Agent": "MatchMateAgent/0.1"},
                timeout=min(self.timeout, 8),
            )
            response.raise_for_status()
        except requests.RequestException:
            return ""

        for page in response.json().get("query", {}).get("pages", {}).values():
            thumbnail = page.get("thumbnail", {})
            if thumbnail.get("source"):
                return str(thumbnail["source"])
        return ""

    def _wikidata_entity_id(self, title: str) -> str:
        try:
            response = requests.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities",
                    "search": title,
                    "language": "en",
                    "format": "json",
                    "limit": 1,
                },
                headers={"User-Agent": "MatchMateAgent/0.1"},
                timeout=min(self.timeout, 8),
            )
            response.raise_for_status()
        except requests.RequestException:
            return ""

        results = response.json().get("search", [])
        return str(results[0].get("id") or "") if results else ""

    def _wikidata_logo_url(self, entity_id: str, category: str = "teams") -> str:
        if not entity_id:
            return ""
        try:
            response = requests.get(
                f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json",
                headers={"User-Agent": "MatchMateAgent/0.1"},
                timeout=min(self.timeout, 8),
            )
            response.raise_for_status()
        except requests.RequestException:
            return ""

        entity = response.json().get("entities", {}).get(entity_id, {})
        claims = entity.get("claims", {})
        image_props = ("P41", "P154", "P94") if category == "national_teams" else ("P154",)
        for prop in image_props:
            for claim in claims.get(prop, []):
                filename = (
                    claim.get("mainsnak", {})
                    .get("datavalue", {})
                    .get("value")
                )
                if filename:
                    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(str(filename).replace(' ', '_'), safe='')}?width=160"
        return ""

    def _search_players(self, query: str, terms: dict[str, Any]) -> CatalogSearchResult:
        canonical_query = canonical_term(query, terms, "players")
        response = requests.get(
            f"{self.base_url}/searchplayers.php",
            params={"p": canonical_query},
            timeout=self.timeout,
        )
        response.raise_for_status()
        players = response.json().get("player") or []
        records = []
        names = []
        for player in players:
            name = player.get("strPlayer")
            if not name:
                continue
            aliases = {
                "Heung-min Son": "Son Heung-min",
                "Shohei Ohtani": "Shohei Ohtani",
            }
            name = aliases.get(name, name)
            names.append(name)
            records.append(
                {
                    "category": "players",
                    "id": player.get("idPlayer"),
                    "name": name,
                    "team": player.get("strTeam"),
                    "sport": player.get("strSport"),
                    "nationality": player.get("strNationality"),
                    "thumb": player.get("strThumb"),
                    "source": "thesportsdb",
                }
            )
        is_broad_single_token = len(canonical_query) >= 4 and " " not in canonical_query.strip()
        if len(names) < 5 and is_broad_single_token:
            fallback_names, fallback_records = self._search_wikipedia_athletes(canonical_query)
            known = set(names)
            for name, record in zip(fallback_names, fallback_records, strict=False):
                if name in known:
                    continue
                known.add(name)
                names.append(name)
                records.append(record)

        warning = "" if names else "TheSportsDB와 Wikipedia에서 일치하는 선수를 찾지 못했습니다."
        return CatalogSearchResult("players", query, names[:12], records[:12], "thesportsdb+wikipedia", warning)

    def _search_wikipedia_athletes(self, query: str) -> tuple[list[str], list[dict[str, Any]]]:
        search_terms = [
            f"{query} footballer",
            f"{query} basketball player",
            f"{query} baseball player",
            f"{query} athlete",
        ]
        names = []
        records = []
        seen = set()

        for search_term in search_terms:
            try:
                response = requests.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "format": "json",
                        "srlimit": 8,
                        "srsearch": search_term,
                    },
                    headers={"User-Agent": "MatchMateAgent/0.1"},
                    timeout=min(self.timeout, 8),
                )
                response.raise_for_status()
            except requests.RequestException:
                continue

            for item in response.json().get("query", {}).get("search", []):
                title = str(item.get("title") or "").strip()
                if not title or "disambiguation" in title.casefold() or title.startswith("List of "):
                    continue
                name = re.sub(r"\s*\([^)]*\)", "", title).strip()
                if not name or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                names.append(name)
                records.append(
                    {
                        "category": "players",
                        "id": f"wikipedia:{item.get('pageid')}",
                        "name": name,
                        "title": title,
                        "source": "wikipedia",
                    }
                )
                if len(names) >= 10:
                    self._hydrate_wikipedia_thumbnails(records)
                    return names, records

        self._hydrate_wikipedia_thumbnails(records)
        return names, records

    def _hydrate_wikipedia_thumbnails(self, records: list[dict[str, Any]]) -> None:
        page_ids = [
            str(record.get("id", "")).replace("wikipedia:", "")
            for record in records
            if str(record.get("id", "")).startswith("wikipedia:")
        ]
        if not page_ids:
            return

        try:
            response = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "format": "json",
                    "prop": "pageimages|description",
                    "pageids": "|".join(page_ids[:10]),
                    "pithumbsize": 160,
                },
                headers={"User-Agent": "MatchMateAgent/0.1"},
                timeout=min(self.timeout, 8),
            )
            response.raise_for_status()
        except requests.RequestException:
            return

        pages = response.json().get("query", {}).get("pages", {})
        for record in records:
            page_id = str(record.get("id", "")).replace("wikipedia:", "")
            page = pages.get(page_id, {})
            thumbnail = page.get("thumbnail", {})
            if thumbnail.get("source"):
                record["thumb"] = thumbnail["source"]
            if page.get("description"):
                record["description"] = page["description"]
