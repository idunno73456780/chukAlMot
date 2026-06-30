from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_PROFILE: dict[str, Any] = {
    "name": "",
    "email": "",
    "timezone": "Asia/Seoul",
    "region": "서울, 대한민국",
    "workdays": [0, 1, 2, 3, 4],
    "work_start": "09:00",
    "work_end": "18:00",
    "sleep_start": "00:30",
    "wake_time": "07:30",
    "minimum_sleep_hours": 5.5,
    "calendar_reminder_minutes": 60,
    "important_match_threshold": 70,
}

DEFAULT_INTERESTS: dict[str, Any] = {
    "sports": [],
    "teams": [],
    "players": [],
    "national_teams": [],
    "competitions": [],
    "include_important_unfollowed_matches": True,
}

TAG_SCORES = {
    "final": 60,
    "semifinal": 48,
    "quarterfinal": 38,
    "playoff": 45,
    "knockout": 36,
    "derby": 28,
    "rivalry": 24,
    "top_ranked": 25,
    "title_race": 24,
    "relegation": 20,
    "national_team": 34,
    "world_cup": 40,
    "champions_league": 38,
    "opening_day": 14,
    "prime_time": 10,
    "record_watch": 22,
    "game_7": 55,
}

TAG_LABELS = {
    "final": "결승전",
    "semifinal": "준결승",
    "quarterfinal": "8강전",
    "playoff": "플레이오프",
    "knockout": "토너먼트 탈락 결정 경기",
    "derby": "라이벌/더비 매치",
    "rivalry": "라이벌전",
    "top_ranked": "상위권 맞대결",
    "title_race": "우승 경쟁 영향",
    "relegation": "강등권 경쟁 영향",
    "national_team": "국가대표 경기",
    "world_cup": "월드컵급 대회",
    "champions_league": "챔피언스리그급 경기",
    "opening_day": "개막전",
    "prime_time": "현지 주요 시간대 경기",
    "record_watch": "기록 달성 가능성",
    "game_7": "시리즈 최종전",
}

VIEWING_BUCKETS = [
    ("관람 가능성 높음", 75),
    ("약간 무리하면 가능", 45),
    ("많이 무리해야 함", -999),
]


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return copy.deepcopy(default)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_list(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[\n,]", value)
    return [part.strip() for part in parts if part.strip()]


def normalize_text(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[\s\-_.,:()'\"/]+", "", text)
    return text


def text_matches(candidate: str, targets: list[str]) -> bool:
    normalized_candidate = normalize_text(candidate)
    if not normalized_candidate:
        return False
    for target in targets:
        normalized_target = normalize_text(target)
        if not normalized_target:
            continue
        if normalized_target in normalized_candidate or normalized_candidate in normalized_target:
            return True
    return False


def parse_utc_datetime(value: str) -> datetime:
    raw = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_local_datetime(value: str, tz_name: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed.astimezone(ZoneInfo(tz_name))


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")[:2]
    return time(int(hour), int(minute))


def event_id(event: dict[str, Any]) -> str:
    if event.get("id"):
        return str(event["id"])
    raw = "|".join(
        [
            str(event.get("sport", "")),
            str(event.get("league", "")),
            str(event.get("home_team", "")),
            str(event.get("away_team", "")),
            str(event.get("start_time_utc", "")),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def event_title(event: dict[str, Any]) -> str:
    home = event.get("home_team") or "TBD"
    away = event.get("away_team") or "TBD"
    league = event.get("league") or event.get("sport") or "Sports"
    return f"{home} vs {away} - {league}"


def get_event_teams(event: dict[str, Any]) -> list[str]:
    teams = list(event.get("teams") or [])
    for key in ("home_team", "away_team"):
        if event.get(key):
            teams.append(str(event[key]))
    return sorted(set(teams))


def interest_match_reasons(event: dict[str, Any], interests: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    teams = get_event_teams(event)
    players = list(event.get("players") or [])
    national_teams = list(event.get("national_teams") or [])
    league = str(event.get("league") or "")

    if any(text_matches(team, interests.get("teams", [])) for team in teams):
        reasons.append("관심 팀 경기")
    if any(text_matches(player, interests.get("players", [])) for player in players):
        reasons.append("관심 선수 관련 경기")
    if any(text_matches(team, interests.get("national_teams", [])) for team in national_teams + teams):
        reasons.append("관심 국가대표 경기")
    if text_matches(league, interests.get("competitions", [])):
        reasons.append("관심 대회 경기")
    return sorted(set(reasons))


def is_interest_event(event: dict[str, Any], interests: dict[str, Any]) -> bool:
    return bool(interest_match_reasons(event, interests))


def importance_score(event: dict[str, Any], interests: dict[str, Any]) -> dict[str, Any]:
    score = int(event.get("importance_score_hint") or 0)
    reasons: list[str] = []
    tags = [str(tag).casefold() for tag in event.get("importance_tags", [])]

    for tag in tags:
        points = TAG_SCORES.get(tag, 0)
        if points:
            score += points
            reasons.append(TAG_LABELS.get(tag, tag))

    match_reasons = interest_match_reasons(event, interests)
    if match_reasons:
        score += 35
        reasons.extend(match_reasons)

    if text_matches(str(event.get("sport", "")), interests.get("sports", [])):
        score += 8
        reasons.append("관심 종목")

    stage = normalize_text(event.get("stage", ""))
    if "final" in stage or "결승" in stage:
        score += 30
        reasons.append("중요 라운드")
    elif "semi" in stage or "준결승" in stage:
        score += 22
        reasons.append("중요 라운드")

    score = max(0, min(score, 100))
    return {
        "score": score,
        "reasons": unique_preserve_order(reasons) or ["일정 중요도 기본 점수"],
    }


def unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def add_local_time(event: dict[str, Any], tz_name: str) -> dict[str, Any]:
    enriched = copy.deepcopy(event)
    start_utc = parse_utc_datetime(str(enriched["start_time_utc"]))
    duration = int(enriched.get("estimated_duration_minutes") or 120)
    start_local = start_utc.astimezone(ZoneInfo(tz_name))
    end_local = start_local + timedelta(minutes=duration)
    enriched["id"] = event_id(enriched)
    enriched["title"] = event_title(enriched)
    enriched["local_start"] = start_local.isoformat()
    enriched["local_end"] = end_local.isoformat()
    enriched["local_date"] = start_local.strftime("%Y-%m-%d")
    enriched["local_time"] = start_local.strftime("%H:%M")
    enriched["local_weekday"] = ["월", "화", "수", "목", "금", "토", "일"][start_local.weekday()]
    enriched["display_time"] = start_local.strftime("%Y-%m-%d (%a) %H:%M")
    return enriched


def overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def work_window_for(day: date, profile: dict[str, Any], tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    if day.weekday() not in profile.get("workdays", []):
        return None
    start = datetime.combine(day, parse_hhmm(profile.get("work_start", "09:00")), tzinfo=tz)
    end = datetime.combine(day, parse_hhmm(profile.get("work_end", "18:00")), tzinfo=tz)
    return start, end


def sleep_hours_after_event(end_local: datetime, profile: dict[str, Any], tz: ZoneInfo) -> float:
    wake_time = parse_hhmm(profile.get("wake_time", "07:30"))
    wake_date = end_local.date()
    if end_local.time() >= time(12, 0):
        wake_date = wake_date + timedelta(days=1)
    wake_dt = datetime.combine(wake_date, wake_time, tzinfo=tz)
    if wake_dt <= end_local:
        wake_dt += timedelta(days=1)
    return max(0.0, (wake_dt - end_local).total_seconds() / 3600)


def busy_conflicts(
    start_local: datetime,
    end_local: datetime,
    busy_blocks: list[dict[str, Any]],
    tz_name: str,
) -> list[dict[str, Any]]:
    conflicts = []
    for block in busy_blocks:
        try:
            block_start = parse_local_datetime(str(block["start_local"]), tz_name)
            block_end = parse_local_datetime(str(block["end_local"]), tz_name)
        except (KeyError, ValueError):
            continue
        if overlaps(start_local, end_local, block_start, block_end):
            conflicts.append(block)
    return conflicts


def next_morning_busy(
    end_local: datetime,
    busy_blocks: list[dict[str, Any]],
    tz_name: str,
) -> list[dict[str, Any]]:
    target_date = (end_local + timedelta(days=1)).date()
    result = []
    for block in busy_blocks:
        try:
            block_start = parse_local_datetime(str(block["start_local"]), tz_name)
        except (KeyError, ValueError):
            continue
        if block_start.date() == target_date and block_start.time() < time(11, 0):
            result.append(block)
    return result


def viewing_assessment(
    event: dict[str, Any],
    profile: dict[str, Any],
    busy_blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    tz_name = profile.get("timezone", "Asia/Seoul")
    tz = ZoneInfo(tz_name)
    start_local = parse_local_datetime(str(event["local_start"]), tz_name)
    end_local = parse_local_datetime(str(event["local_end"]), tz_name)

    score = 55
    reasons: list[str] = []
    cautions: list[str] = []

    conflicts = busy_conflicts(start_local, end_local, busy_blocks, tz_name)
    if conflicts:
        score -= 45
        cautions.append("기존 일정과 시간이 겹칩니다.")
    else:
        score += 18
        reasons.append("캘린더상 직접 충돌이 없습니다.")

    work_window = work_window_for(start_local.date(), profile, tz)
    if work_window and overlaps(start_local, end_local, work_window[0], work_window[1]):
        score -= 42
        cautions.append("평일 업무시간과 겹칩니다.")
    elif work_window and start_local >= work_window[1]:
        score += 18
        reasons.append("퇴근 후 관람 가능한 시간대입니다.")
    elif not work_window:
        score += 16
        reasons.append("업무일이 아닌 날에 열립니다.")

    if time(18, 0) <= start_local.time() <= time(22, 30):
        score += 18
        reasons.append("저녁 시간대 경기입니다.")
    elif start_local.time() >= time(23, 0) or start_local.time() < time(5, 30):
        score -= 18
        cautions.append("늦은 밤 또는 새벽 경기입니다.")

    tomorrow = start_local.date() + timedelta(days=1)
    if is_weekend(tomorrow):
        score += 12
        reasons.append("다음날이 주말이라 부담이 낮습니다.")

    morning_blocks = next_morning_busy(end_local, busy_blocks, tz_name)
    if morning_blocks:
        score -= 25
        cautions.append("다음날 오전 일정이 있어 피로 부담이 큽니다.")

    minimum_sleep = float(profile.get("minimum_sleep_hours", 5.5))
    if start_local.time() >= time(22, 0) or start_local.time() < time(5, 30):
        sleep_hours = sleep_hours_after_event(end_local, profile, tz)
        if sleep_hours < minimum_sleep:
            score -= 25
            cautions.append(f"예상 수면 시간이 {sleep_hours:.1f}시간 정도로 짧습니다.")
        else:
            score += 8
            reasons.append(f"경기 후 약 {sleep_hours:.1f}시간 수면 여유가 있습니다.")

    score = max(0, min(100, score))
    bucket = "많이 무리해야 함"
    for label, threshold in VIEWING_BUCKETS:
        if score >= threshold:
            bucket = label
            break

    return {
        "score": score,
        "bucket": bucket,
        "reasons": unique_preserve_order(reasons),
        "cautions": unique_preserve_order(cautions),
        "conflicts": conflicts,
    }


def filter_events_by_date(
    events: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    result = []
    for event in events:
        if not event.get("start_time_utc"):
            continue
        event_date = parse_utc_datetime(str(event["start_time_utc"])).date()
        if start_date <= event_date <= end_date:
            result.append(event)
    return result


def analyze_events(
    events: list[dict[str, Any]],
    profile: dict[str, Any],
    interests: dict[str, Any],
    busy_blocks: list[dict[str, Any]],
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    tz_name = profile.get("timezone", "Asia/Seoul")
    now_utc = now_utc or datetime.now(timezone.utc)
    enriched = [add_local_time(event, tz_name) for event in events if event.get("start_time_utc")]
    enriched.sort(key=lambda item: item["start_time_utc"])

    scheduled = []
    completed = []
    for event in enriched:
        status = str(event.get("status", "scheduled")).casefold()
        if status in {"final", "completed", "finished"}:
            completed.append(event)
        else:
            event_start = parse_utc_datetime(str(event["start_time_utc"]))
            if event_start >= now_utc - timedelta(hours=3):
                scheduled.append(event)
            else:
                completed.append(event)

    for event in enriched:
        event["interest_reasons"] = interest_match_reasons(event, interests)
        event["is_interest_event"] = bool(event["interest_reasons"])
        event["importance"] = importance_score(event, interests)
        if event in scheduled:
            event["viewing"] = viewing_assessment(event, profile, busy_blocks)

    calendar_candidates = [event for event in scheduled if event["is_interest_event"]]
    important_threshold = int(profile.get("important_match_threshold", 70))
    important_unfollowed = [
        event
        for event in scheduled
        if not event["is_interest_event"] and event["importance"]["score"] >= important_threshold
    ]

    viewing_groups: dict[str, list[dict[str, Any]]] = {label: [] for label, _ in VIEWING_BUCKETS}
    for event in calendar_candidates:
        viewing_groups[event["viewing"]["bucket"]].append(event)

    return {
        "generated_at": datetime.now(ZoneInfo(tz_name)).isoformat(),
        "profile_timezone": tz_name,
        "scheduled_events": scheduled,
        "completed_events": completed,
        "calendar_candidates": calendar_candidates,
        "important_unfollowed": important_unfollowed,
        "viewing_groups": viewing_groups,
        "summary": {
            "scheduled_count": len(scheduled),
            "completed_count": len(completed),
            "calendar_candidate_count": len(calendar_candidates),
            "important_unfollowed_count": len(important_unfollowed),
        },
    }


def event_result_line(event: dict[str, Any]) -> str:
    score = event.get("score")
    if isinstance(score, dict):
        result = f"{score.get('home', '-')}-{score.get('away', '-')}"
    else:
        result = "결과 정보 없음"
    return f"{event['display_time']} | {event_title(event)} | {result}"


def event_schedule_line(event: dict[str, Any]) -> str:
    broadcast = ", ".join(event.get("broadcast", []) or ["중계 정보 확인 필요"])
    return f"{event['display_time']} | {event_title(event)} | {broadcast}"


def standings_markdown(standings: list[dict[str, Any]], interests: dict[str, Any]) -> str:
    sections = []
    for table in standings:
        rows = table.get("rows", [])
        if not rows:
            continue
        league = table.get("league", "Standings")
        lines = [f"### {league}"]
        for row in rows[:8]:
            team = str(row.get("team", ""))
            marker = " *" if text_matches(team, interests.get("teams", []) + interests.get("national_teams", [])) else ""
            played = row.get("played", "-")
            points = row.get("points", row.get("wins", "-"))
            lines.append(f"- {row.get('rank', '-')}. {team}{marker} | 경기 {played} | 포인트/승 {points}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) if sections else "순위표 데이터가 없습니다."


def brackets_markdown(brackets: list[dict[str, Any]]) -> str:
    if not brackets:
        return "토너먼트 대진표 데이터가 없습니다."
    sections = []
    for bracket in brackets:
        lines = [f"### {bracket.get('competition', 'Tournament')} - {bracket.get('stage', '')}".strip()]
        for match in bracket.get("matches", []):
            status = match.get("status", "scheduled")
            result = match.get("result", "예정")
            lines.append(f"- {match.get('slot', '-')}: {match.get('home', 'TBD')} vs {match.get('away', 'TBD')} | {status} | {result}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def generate_weekly_newsletter(
    analysis: dict[str, Any],
    profile: dict[str, Any],
    interests: dict[str, Any],
    standings: list[dict[str, Any]] | None = None,
    brackets: list[dict[str, Any]] | None = None,
) -> str:
    name = profile.get("name") or "MatchMate User"
    completed_interest = [
        event for event in analysis["completed_events"] if event.get("is_interest_event")
    ]
    upcoming_interest = analysis["calendar_candidates"]
    important_unfollowed = analysis["important_unfollowed"]

    lines = [
        f"# MatchMate Weekly Brief for {name}",
        "",
        f"생성 시각: {analysis['generated_at']}",
        "",
        "## 지난주 관심 경기 결과",
    ]

    if completed_interest:
        for event in completed_interest[:10]:
            lines.append(f"- {event_result_line(event)}")
    else:
        lines.append("- 지난주 관심 경기 결과가 없습니다.")

    lines.extend(["", "## 관심 팀/대회 순위표", standings_markdown(standings or [], interests)])
    lines.extend(["", "## 토너먼트 대진표 현황", brackets_markdown(brackets or [])])
    lines.extend(["", "## 이번 주 관심 경기"])

    if upcoming_interest:
        for event in upcoming_interest[:12]:
            viewing = event.get("viewing", {})
            lines.append(
                f"- {event_schedule_line(event)} | 관람 판단: {viewing.get('bucket', '분석 없음')} "
                f"({viewing.get('score', '-')}점)"
            )
    else:
        lines.append("- 이번 주 관심 경기 일정이 없습니다.")

    lines.extend(["", "## 관심 밖이지만 중요한 경기 추천"])
    if interests.get("include_important_unfollowed_matches", True) and important_unfollowed:
        for event in important_unfollowed[:8]:
            importance = event["importance"]
            lines.append(
                f"- {event_schedule_line(event)} | 중요도 {importance['score']}점: "
                f"{', '.join(importance['reasons'][:3])}"
            )
    else:
        lines.append("- 이번 주에는 별도 추천할 주요 경기가 없습니다.")

    return "\n".join(lines).strip() + "\n"


def summarize_event_for_email(event: dict[str, Any]) -> str:
    viewing = event.get("viewing", {})
    reasons = viewing.get("reasons") or []
    cautions = viewing.get("cautions") or []
    detail = []
    if reasons:
        detail.append("좋은 점: " + ", ".join(reasons[:2]))
    if cautions:
        detail.append("주의: " + ", ".join(cautions[:2]))
    detail_text = " / ".join(detail) if detail else "상세 판단 정보 없음"
    return (
        f"- {event_schedule_line(event)} | {viewing.get('bucket', '분석 없음')} "
        f"({viewing.get('score', '-')}점) | {detail_text}"
    )


def generate_viewing_email(analysis: dict[str, Any], profile: dict[str, Any]) -> str:
    lines = [
        f"# 이번 주 관람 가능 경기 안내 - {profile.get('name', 'MatchMate User')}",
        "",
        f"기준 시간대: {profile.get('timezone', 'Asia/Seoul')}",
        "",
    ]
    for label, _ in VIEWING_BUCKETS:
        lines.append(f"## {label}")
        events = analysis["viewing_groups"].get(label, [])
        if not events:
            lines.append("- 해당 경기 없음")
        else:
            for event in events:
                lines.append(summarize_event_for_email(event))
        lines.append("")
    return "\n".join(lines).strip() + "\n"
