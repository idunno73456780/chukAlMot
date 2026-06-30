from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests

from matchmate_agent import filter_events_by_date, load_json, text_matches
from runtime_config import config_bool, config_csv, config_int, config_value


@dataclass
class SportsFetchResult:
    source: str
    events: list[dict[str, Any]]
    standings: list[dict[str, Any]]
    brackets: list[dict[str, Any]]
    warnings: list[str]


class SportsApiClient:
    def __init__(self, sample_path: Path, sample_mode: bool = False, timeout: int = 12):
        self.sample_path = sample_path
        self.sample_mode = sample_mode
        self.timeout = timeout
        self.allow_sample_fallback = config_bool("MATCHMATE_ALLOW_SAMPLE_FALLBACK", False)
        self.fetch_tv = config_bool("MATCHMATE_FETCH_TV", True)
        self.max_tv_lookups = config_int("MATCHMATE_MAX_TV_LOOKUPS", 2)

    def fetch(
        self,
        interests: dict[str, Any],
        start_date: date,
        end_date: date,
    ) -> SportsFetchResult:
        if self.sample_mode:
            return self._fetch_sample(start_date, end_date, ["내장 데이터 모드가 켜져 있습니다."])

        provider = config_value("SPORTS_API_PROVIDER", "thesportsdb").casefold()
        try:
            if provider == "thesportsdb":
                result = self._fetch_thesportsdb(interests, start_date, end_date)
            else:
                raise ValueError(f"Unsupported SPORTS_API_PROVIDER: {provider}")
            if result.events:
                return result
            result.warnings.append("스포츠 API에서 설정된 관심 대상과 기간에 해당하는 경기를 찾지 못했습니다.")
            if self.allow_sample_fallback:
                sample_result = self._fetch_sample(start_date, end_date, result.warnings + ["내장 데이터 fallback이 켜져 있어 내장 경기 데이터를 표시합니다."])
                sample_result.source = f"{provider}+embedded_fallback"
                return sample_result
            return result
        except Exception as exc:
            warnings = [f"스포츠 API 조회 실패: {exc}"]
            if self.allow_sample_fallback:
                sample_result = self._fetch_sample(start_date, end_date, warnings + ["내장 데이터 fallback이 켜져 있어 내장 경기 데이터를 표시합니다."])
                sample_result.source = f"{provider}+embedded_fallback"
                return sample_result
            return SportsFetchResult(
                source=f"{provider}_error",
                events=[],
                standings=[],
                brackets=[],
                warnings=warnings,
            )

    def _fetch_sample(
        self,
        start_date: date,
        end_date: date,
        warnings: list[str] | None = None,
    ) -> SportsFetchResult:
        data = load_json(self.sample_path, {"events": [], "standings": [], "brackets": []})
        events = filter_events_by_date(data.get("events", []), start_date, end_date)
        return SportsFetchResult(
            source="embedded",
            events=events,
            standings=data.get("standings", []),
            brackets=data.get("brackets", []),
            warnings=warnings or [],
        )

    def _fetch_thesportsdb(
        self,
        interests: dict[str, Any],
        start_date: date,
        end_date: date,
    ) -> SportsFetchResult:
        api_key = config_value("THESPORTSDB_API_KEY", "123")
        base_url = f"https://www.thesportsdb.com/api/v1/json/{api_key}"
        targets = (
            interests.get("teams", [])
            + interests.get("national_teams", [])
        )
        tracked_league_ids = config_csv("MATCHMATE_IMPORTANT_LEAGUE_IDS", "4328,4480,4387,4424")
        collected: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        league_ids: set[str] = set(tracked_league_ids)

        for target in targets[:8]:
            team_id = self._thesportsdb_team_id(base_url, target)
            if not team_id:
                warnings.append(f"Team not found in TheSportsDB: {target}")
                continue
            for endpoint in ("eventsnext.php", "eventslast.php"):
                response = requests.get(
                    f"{base_url}/{endpoint}",
                    params={"id": team_id},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                for item in response.json().get("events") or []:
                    event = self._normalize_thesportsdb_event(item)
                    if not event:
                        continue
                    if item.get("idLeague"):
                        league_ids.add(str(item["idLeague"]))
                    if start_date <= _event_date(event) <= end_date:
                        collected[event["id"]] = event

        for league_id in tracked_league_ids[:8]:
            for endpoint in ("eventsnextleague.php", "eventspastleague.php"):
                response = requests.get(
                    f"{base_url}/{endpoint}",
                    params={"id": league_id},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                for item in response.json().get("events") or []:
                    event = self._normalize_thesportsdb_event(item)
                    if not event:
                        continue
                    event.setdefault("importance_tags", [])
                    if endpoint == "eventsnextleague.php":
                        event["importance_tags"] = list(set(event.get("importance_tags", []) + ["tracked_league"]))
                    if item.get("idLeague"):
                        league_ids.add(str(item["idLeague"]))
                    if start_date <= _event_date(event) <= end_date:
                        collected[event["id"]] = event

        events = sorted(collected.values(), key=lambda item: item["start_time_utc"])
        if self.fetch_tv and events and self.max_tv_lookups > 0:
            self._attach_thesportsdb_tv(base_url, events[: self.max_tv_lookups])

        return SportsFetchResult(
            source="thesportsdb",
            events=events,
            standings=self._fetch_thesportsdb_standings(base_url, sorted(league_ids)[:6]),
            brackets=[],
            warnings=warnings,
        )

    def _thesportsdb_team_id(self, base_url: str, target: str) -> str | None:
        response = requests.get(
            f"{base_url}/searchteams.php",
            params={"t": target},
            timeout=self.timeout,
        )
        response.raise_for_status()
        teams = response.json().get("teams") or []
        if not teams:
            return None
        for team in teams:
            if text_matches(team.get("strTeam", ""), [target]):
                return team.get("idTeam")
        return teams[0].get("idTeam")

    def _normalize_thesportsdb_event(self, item: dict[str, Any]) -> dict[str, Any] | None:
        timestamp = item.get("strTimestamp")
        if not timestamp:
            date_part = item.get("dateEvent")
            time_part = item.get("strTime") or "00:00:00"
            if not date_part:
                return None
            timestamp = f"{date_part}T{time_part}Z"
        if timestamp.endswith("+00:00"):
            timestamp = timestamp.replace("+00:00", "Z")

        home = item.get("strHomeTeam") or "TBD"
        away = item.get("strAwayTeam") or "TBD"
        status = item.get("strStatus") or "scheduled"
        score = None
        if item.get("intHomeScore") not in (None, "") or item.get("intAwayScore") not in (None, ""):
            status = "completed"
            score = {
                "home": item.get("intHomeScore", "-"),
                "away": item.get("intAwayScore", "-"),
            }

        stage = item.get("strRound") or item.get("intRound") or ""
        league = item.get("strLeague") or "Unknown League"
        summary = item.get("strEvent") or f"{home} vs {away}"

        return {
            "id": item.get("idEvent"),
            "sport": item.get("strSport") or "Sports",
            "league": league,
            "league_id": item.get("idLeague") or "",
            "home_team": home,
            "away_team": away,
            "teams": [home, away],
            "players": [],
            "national_teams": [],
            "start_time_utc": timestamp,
            "estimated_duration_minutes": 120,
            "venue": item.get("strVenue") or "",
            "status": status,
            "score": score,
            "stage": str(stage),
            "importance_tags": self._importance_tags(item, league, summary, stage),
            "broadcast": [],
            "external_url": item.get("strEventAlternate") or "",
            "summary": summary,
        }

    def _importance_tags(self, item: dict[str, Any], league: str, summary: str, stage: Any) -> list[str]:
        haystack = " ".join(
            [
                str(league or ""),
                str(summary or ""),
                str(stage or ""),
                str(item.get("strSeason") or ""),
            ]
        ).casefold()
        tags = []
        if "world cup" in haystack:
            tags.extend(["world_cup", "national_team"])
        if "champions league" in haystack:
            tags.append("champions_league")
        if "final" in haystack:
            tags.append("final")
        if "semi" in haystack:
            tags.append("semifinal")
        if "quarter" in haystack:
            tags.append("quarterfinal")
        if "playoff" in haystack or "play-off" in haystack:
            tags.append("playoff")
        if "derby" in haystack or "rival" in haystack:
            tags.append("derby")
        return sorted(set(tags))

    def _attach_thesportsdb_tv(self, base_url: str, events: list[dict[str, Any]]) -> None:
        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue
            try:
                response = requests.get(
                    f"{base_url}/lookuptv.php",
                    params={"id": event_id},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                channels = []
                for item in response.json().get("tvevent") or []:
                    channel = item.get("strChannel") or item.get("strNetwork")
                    country = item.get("strCountry")
                    if not channel:
                        continue
                    label = f"{channel} ({country})" if country else channel
                    if label not in channels:
                        channels.append(label)
                if channels:
                    event["broadcast"] = channels[:5]
            except Exception:
                continue

    def _fetch_thesportsdb_standings(self, base_url: str, league_ids: list[str]) -> list[dict[str, Any]]:
        standings = []
        season = config_value("MATCHMATE_SEASON", "")
        for league_id in league_ids:
            try:
                params = {"l": league_id}
                if season:
                    params["s"] = season
                response = requests.get(
                    f"{base_url}/lookuptable.php",
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                rows = response.json().get("table") or []
            except Exception:
                continue
            if not rows:
                continue
            league_name = rows[0].get("strLeague") or f"League {league_id}"
            normalized_rows = []
            for item in rows:
                normalized_rows.append(
                    {
                        "rank": item.get("intRank") or item.get("intPosition") or "-",
                        "team": item.get("strTeam") or item.get("strTeamShort") or "-",
                        "played": item.get("intPlayed") or item.get("intFormedYear") or "-",
                        "points": item.get("intPoints") or item.get("intWin") or "-",
                    }
                )
            standings.append({"league": league_name, "rows": normalized_rows})
        return standings


def _event_date(event: dict[str, Any]) -> date:
    from matchmate_agent import parse_utc_datetime

    return parse_utc_datetime(str(event["start_time_utc"])).date()
