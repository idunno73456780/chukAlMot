from __future__ import annotations

import hashlib
import html
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

import streamlit as st
import streamlit.components.v1 as components

from google_calendar_client import registry_record, sync_calendar_event
from google_oauth_client import (
    MATCHMATE_CALENDAR_SUMMARY,
    build_google_oauth_url,
    clear_local_oauth_config,
    disconnect_google_oauth,
    ensure_matchmate_calendar,
    exchange_google_oauth_code,
    fetch_oauth_user_email,
    get_dedicated_calendar_id,
    get_oauth_client_id,
    get_oauth_client_secret,
    get_oauth_config_value,
    get_oauth_redirect_uri,
    get_stored_oauth_user_email,
    google_oauth_configured,
    google_oauth_connected,
    new_oauth_state,
    oauth_token_has_calendar_management_scope,
    save_local_oauth_config,
)
from localization import (
    canonical_terms,
    display_event_meta,
    display_event_title,
    display_term,
    load_terms,
    normalize,
)
from runtime_config import config_bool
from matchmate_agent import (
    DEFAULT_INTERESTS,
    DEFAULT_PROFILE,
    analyze_events,
    generate_viewing_email,
    generate_weekly_newsletter,
    interest_match_reasons,
    load_json,
    parse_local_datetime,
    save_json,
)
from sports_catalog_client import DEFAULT_CATALOG_CACHE, SportsCatalogClient
from sports_api_client import SportsApiClient


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "sample_sports_events.json"
CATALOG_PATH = ROOT / "data" / "sports_catalog.json"
KOREAN_TERMS_PATH = ROOT / "data" / "korean_terms.json"
LOCATION_TIMEZONES_PATH = ROOT / "data" / "location_timezones.json"
STORAGE = ROOT / "storage"
CATALOG_CACHE_PATH = STORAGE / "sports_catalog_cache.json"
PROFILE_PATH = STORAGE / "user_profile.json"
INTERESTS_PATH = STORAGE / "sports_interests.json"
BUSY_BLOCKS_PATH = STORAGE / "busy_blocks.json"
CALENDAR_REGISTRY_PATH = STORAGE / "calendar_registry.json"
LAST_RUN_PATH = STORAGE / "last_run.json"
NEWSLETTER_PREVIEW_PATH = STORAGE / "latest_newsletter.md"
VIEWING_EMAIL_PREVIEW_PATH = STORAGE / "latest_viewing_email.md"
PHOTO_SEARCHBOX_COMPONENT_PATH = ROOT / "components" / "photo_searchbox"

APP_TITLE = "나이제축알못아니야"
APP_SUBTITLE = "관심 스포츠 일정을 Google Calendar와 Gmail 알림으로 이어주는 AI Agent"
CATALOG_SEARCH_CACHE_VERSION = "photo-dropdown-v5-club-logo"
LOCATION_SEARCH_CACHE_VERSION = "location-timezone-v1"
photo_searchbox_component = components.declare_component(
    "photo_searchbox",
    path=str(PHOTO_SEARCHBOX_COMPONENT_PATH),
)

DAY_LABELS = {
    "월": 0,
    "화": 1,
    "수": 2,
    "목": 3,
    "금": 4,
    "토": 5,
    "일": 6,
}

TIMEZONE_DISPLAY_NAMES = {
    "Asia/Seoul": "한국 표준시",
    "Asia/Tokyo": "일본 표준시",
    "Asia/Shanghai": "중국 표준시",
    "Asia/Taipei": "대만 표준시",
    "Asia/Hong_Kong": "홍콩 시간",
    "Asia/Singapore": "싱가포르 시간",
    "Asia/Bangkok": "인도차이나 시간",
    "Asia/Ho_Chi_Minh": "베트남 시간",
    "Asia/Manila": "필리핀 시간",
    "Asia/Jakarta": "서인도네시아 시간",
    "Asia/Dubai": "걸프 표준시",
    "Australia/Sydney": "시드니 시간",
    "Australia/Melbourne": "멜버른 시간",
    "Pacific/Auckland": "뉴질랜드 시간",
    "Europe/London": "영국 시간",
    "Europe/Paris": "중부유럽 시간",
    "Europe/Berlin": "독일 시간",
    "Europe/Madrid": "스페인 시간",
    "Europe/Rome": "이탈리아 시간",
    "Europe/Amsterdam": "네덜란드 시간",
    "Europe/Istanbul": "튀르키예 시간",
    "America/New_York": "미국 동부 시간",
    "America/Chicago": "미국 중부 시간",
    "America/Denver": "미국 산악 시간",
    "America/Los_Angeles": "미국 태평양 시간",
    "America/Toronto": "토론토 시간",
    "America/Vancouver": "밴쿠버 시간",
    "America/Mexico_City": "멕시코시티 시간",
    "America/Sao_Paulo": "브라질리아 시간",
    "America/Argentina/Buenos_Aires": "아르헨티나 시간",
    "UTC": "협정 세계시",
}

PREFERRED_TIMEZONES = tuple(TIMEZONE_DISPLAY_NAMES.keys())

REGION_STATE_KEY = "profile_region_selected"
TIMEZONE_STATE_KEY = "profile_timezone_selected"


def initialize_storage() -> None:
    STORAGE.mkdir(parents=True, exist_ok=True)
    if not PROFILE_PATH.exists():
        save_json(PROFILE_PATH, DEFAULT_PROFILE)
    if not INTERESTS_PATH.exists():
        save_json(INTERESTS_PATH, DEFAULT_INTERESTS)
    if not BUSY_BLOCKS_PATH.exists():
        save_json(BUSY_BLOCKS_PATH, [])
    if not CALENDAR_REGISTRY_PATH.exists():
        save_json(CALENDAR_REGISTRY_PATH, {})
    if not CATALOG_CACHE_PATH.exists():
        save_json(CATALOG_CACHE_PATH, DEFAULT_CATALOG_CACHE)


def load_interest_catalog(interests: dict) -> dict[str, list[str]]:
    catalog = load_json(CATALOG_PATH, {"teams": [], "players": [], "national_teams": []})
    cache = load_json(CATALOG_CACHE_PATH, DEFAULT_CATALOG_CACHE)
    sample = load_json(DATA_PATH, {"events": []})
    teams = set(catalog.get("teams", []))
    players = set(catalog.get("players", []))
    national_teams = set(catalog.get("national_teams", []))

    for event in sample.get("events", []):
        teams.update(event.get("teams", []))
        for key in ("home_team", "away_team"):
            if event.get(key):
                teams.add(str(event[key]))
        players.update(event.get("players", []))
        national_teams.update(event.get("national_teams", []))

    teams.update(interests.get("teams", []))
    players.update(interests.get("players", []))
    national_teams.update(interests.get("national_teams", []))
    teams.update(cache.get("teams", []))
    players.update(cache.get("players", []))
    national_teams.update(cache.get("national_teams", []))

    return {
        "teams": sorted(item for item in teams if item),
        "players": sorted(item for item in players if item),
        "national_teams": sorted(item for item in national_teams if item),
    }


def local_catalog_matches(
    category: str,
    query: str,
    catalog: dict[str, list[str]],
    terms: dict,
    limit: int = 8,
) -> list[str]:
    query_text = str(query or "").strip()
    if not query_text:
        return []
    from localization import normalize

    needle = normalize(query_text)
    matches = []
    for item in catalog.get(category, []):
        display = display_term(item, terms, category)
        haystack = normalize(" ".join([item, display]))
        aliases = terms.get("aliases", {}).get(category, {}).get(item, [])
        alias_text = normalize(" ".join(str(alias) for alias in aliases))
        if needle in haystack or needle in alias_text:
            matches.append(item)
    return matches[:limit]


def image_url_looks_like_logo(value: str) -> bool:
    lowered = str(value or "").casefold()
    return any(marker in lowered for marker in ("logo", "crest", "badge", "insignia", "emblem", "flag_"))


def catalog_records_by_name(category: str) -> dict[str, dict]:
    cache = load_json(CATALOG_CACHE_PATH, DEFAULT_CATALOG_CACHE)
    records = {}

    def image_priority(record: dict) -> int:
        has_image = bool(record.get("thumb") or record.get("badge"))
        if not has_image:
            return 0
        image_url = str(record.get("badge") or record.get("thumb") or "")
        if category == "national_teams":
            image_kind = str(record.get("image_kind") or "")
            record_id = str(record.get("id") or "").casefold()
            if image_kind.startswith("national_team") and image_url_looks_like_logo(image_url):
                return 4
            if record.get("source") == "thesportsdb":
                return 3
            if "national" in record_id:
                return 2
            return 1
        image_kind = str(record.get("image_kind") or "")
        if image_kind == "club_logo" and image_url_looks_like_logo(image_url):
            return 5
        if record.get("source") == "thesportsdb":
            return 4
        return 1

    for record in cache.get("records", []):
        if record.get("category") != category:
            continue
        name = str(record.get("name") or "")
        if not name:
            continue
        current = records.get(name)
        if current is None or image_priority(record) > image_priority(current):
            records[name] = record
    return records


@st.cache_data(ttl=3600, show_spinner=False)
def cached_external_catalog_search(category: str, query: str, cache_version: str = CATALOG_SEARCH_CACHE_VERSION) -> dict:
    client = SportsCatalogClient(CATALOG_CACHE_PATH)
    terms = load_terms(KOREAN_TERMS_PATH)
    result = client.search_and_cache(category, query, terms)
    return {
        "names": result.names,
        "records": result.records,
        "warning": result.warning,
        "source": result.source,
        "version": cache_version,
    }


def inject_styles() -> None:
    st.markdown(
        """
        <style>
          .block-container {
            max-width: 1120px;
            padding-top: 2.75rem;
            padding-bottom: 3rem;
          }
          h1, h2, h3, p { letter-spacing: 0; }
          h1, h2, h3 {
            color: #ffffff !important;
          }
          a[href^="#"] {
            display: none !important;
          }
          div[data-testid="stAlert"] {
            background: #111827 !important;
            border: 1px solid rgba(255, 255, 255, 0.16) !important;
            border-radius: 8px !important;
            color: #ffffff !important;
          }
          div[data-testid="stAlert"] * {
            color: #ffffff !important;
          }
          div[data-testid="stAlert"] svg {
            fill: #ffffff !important;
            stroke: #ffffff !important;
          }
          div[data-testid="stLinkButton"] a,
          div[data-testid="stLinkButton"] a:visited,
          div[data-testid="stLinkButton"] a:hover,
          div[data-testid="stButton"] button[kind="primary"],
          div[data-testid="stButton"] button[kind="primary"]:hover {
            background: #111827 !important;
            border-color: #111827 !important;
            color: #ffffff !important;
          }
          div[data-testid="stLinkButton"] a *,
          div[data-testid="stButton"] button[kind="primary"] * {
            color: #ffffff !important;
          }
          div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 8px;
          }
          .hero {
            align-items: center;
            background: #111827;
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.1);
            display: flex;
            margin-bottom: 1.25rem;
            min-height: 3.2rem;
            padding: 0.7rem 0.85rem;
          }
          .hero-title {
            color: #ffffff !important;
            font-size: 1.45rem !important;
            font-weight: 780 !important;
            line-height: 1.2 !important;
            margin: 0 !important;
          }
          .google-panel {
            background: linear-gradient(135deg, #111827 0%, #374151 100%);
            border-radius: 8px;
            color: #fff;
            margin-bottom: 1.25rem;
            padding: 1.25rem;
          }
          .google-panel h2 {
            color: #fff;
            font-size: 1.55rem;
            margin: 0 0 0.25rem 0;
          }
          .google-panel p {
            color: rgba(255, 255, 255, 0.82);
            margin: 0;
          }
          .pill {
            background: #f3f4f6;
            border: 1px solid #e5e7eb;
            border-radius: 999px;
            color: #374151;
            display: inline-block;
            font-size: 0.88rem;
            font-weight: 650;
            margin: 0.15rem 0.25rem 0.15rem 0;
            padding: 0.25rem 0.65rem;
          }
          .empty-pill {
            color: #6b7280;
            font-size: 0.92rem;
          }
          .section-label {
            color: #6b7280;
            font-size: 0.9rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
            text-transform: uppercase;
          }
          .scoreline {
            color: #374151;
            font-size: 0.95rem;
          }
          .section-heading {
            align-items: center;
            background: #111827;
            border-radius: 8px;
            color: #ffffff !important;
            display: flex;
            font-size: 1.05rem !important;
            font-weight: 760 !important;
            gap: 0.5rem;
            line-height: 1.3 !important;
            margin: 0 0 0.65rem !important;
            padding: 0.5rem 0.65rem !important;
          }
          .section-heading::before {
            background: #ffffff;
            border-radius: 999px;
            content: "";
            display: inline-block;
            flex: 0 0 0.24rem;
            height: 1.05rem;
          }
          .list-heading {
            align-items: center;
            background: #111827;
            border-radius: 8px;
            color: #ffffff !important;
            display: flex;
            font-size: 1.16rem !important;
            font-weight: 780 !important;
            gap: 0.55rem;
            line-height: 1.3 !important;
            margin: 1.15rem 0 0.55rem !important;
            padding: 0.55rem 0.7rem !important;
          }
          .list-heading::before {
            background: #ffffff;
            border-radius: 999px;
            content: "";
            display: inline-block;
            flex: 0 0 0.26rem;
            height: 1.15rem;
          }
          .section-helper {
            color: #6b7280;
            font-size: 0.9rem;
            line-height: 1.45;
            margin: 0 0 0.9rem;
          }
          div[data-testid="stMarkdownContainer"] a:not(.match-info-link) {
            color: #111827 !important;
          }
          div[data-testid="stMarkdownContainer"] a:not(.match-info-link):hover {
            color: #374151 !important;
          }
          .recommendation-title {
            background: #111827;
            border-radius: 8px;
            color: #ffffff !important;
            display: block;
            font-size: 1.14rem !important;
            font-weight: 760 !important;
            line-height: 1.35 !important;
            margin: 0 0 0.55rem !important;
            padding: 0.55rem 0.65rem !important;
          }
          .recommendation-meta {
            color: #4b5563;
            font-size: 0.9rem !important;
            line-height: 1.5 !important;
            margin: 0 !important;
          }
          .recommendation-matchup {
            align-items: center;
            display: grid;
            gap: 0.85rem;
            grid-template-columns: minmax(6rem, 0.8fr) minmax(0, 2.4fr) minmax(6rem, 0.8fr);
            margin-bottom: 0.8rem;
          }
          .matchup-team {
            align-items: center;
            display: flex;
            flex-direction: column;
            gap: 0.35rem;
            min-width: 0;
            text-align: center;
          }
          .matchup-side {
            color: #6b7280;
            font-size: 0.73rem;
            font-weight: 760;
            line-height: 1;
          }
          .matchup-logo,
          .matchup-logo-fallback {
            align-items: center;
            aspect-ratio: 1;
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            display: flex;
            height: 68px;
            justify-content: center;
            width: 68px;
          }
          .matchup-logo {
            object-fit: contain;
            padding: 0.35rem;
          }
          .matchup-logo-fallback {
            background: #f3f4f6;
            color: #374151;
            font-size: 1rem;
            font-weight: 780;
          }
.matchup-name {
  background: #111827;
  border-radius: 6px;
  color: #ffffff !important;
  display: inline-block;
  font-size: 0.82rem !important;
  font-weight: 760 !important;
  line-height: 1.25 !important;
  margin: 0 !important;
  max-width: 8.2rem;
  overflow-wrap: anywhere;
  padding: 0.16rem 0.42rem !important;
}
          .matchup-center {
            min-width: 0;
          }
          @media (max-width: 760px) {
            .recommendation-matchup {
              grid-template-columns: minmax(4.8rem, 0.8fr) minmax(0, 1.5fr) minmax(4.8rem, 0.8fr);
            }
            .matchup-logo,
            .matchup-logo-fallback {
              height: 56px;
              width: 56px;
            }
            .matchup-name {
              font-size: 0.78rem !important;
            }
          }
          .broadcast-line {
            align-items: center;
            background: #111827;
            border-radius: 8px;
            box-sizing: border-box;
            color: #ffffff !important;
            display: inline-flex;
            flex-wrap: wrap;
            gap: 0;
            font-size: 0.93rem !important;
            line-height: 1.25 !important;
            margin: 0 !important;
            max-width: 100%;
            padding: 0.12rem 0.42rem !important;
            overflow-wrap: anywhere;
          }
          div[data-testid="stElementContainer"]:has(.broadcast-line),
          div[data-testid="stMarkdown"]:has(.broadcast-line),
          div[data-testid="stMarkdownContainer"]:has(.broadcast-line) {
            height: auto !important;
            min-height: 1.7rem !important;
            overflow: visible;
          }
          .broadcast-label {
            color: #ffffff !important;
            font-weight: 760;
            white-space: nowrap;
          }
          .broadcast-label::after {
            content: ":";
            margin-right: 0.25rem;
          }
          .broadcast-text {
            color: #ffffff !important;
            font-size: 0.93rem !important;
            font-weight: 650 !important;
            overflow-wrap: anywhere;
          }
          .recommendation-footer {
            align-items: center;
            display: flex;
            gap: 0.75rem;
            justify-content: flex-end;
            margin-top: 0.15rem;
          }
          .recommendation-footer .broadcast-line {
            flex: 1 1 auto;
            min-width: 0;
          }
          .recommendation-footer .match-info-link {
            flex: 0 0 auto;
          }
          .match-info-link {
            background: #111827;
            border: 1px solid rgba(17, 24, 39, 0.24);
            border-radius: 8px;
            color: #ffffff !important;
            display: inline-flex;
            font-size: 0.9rem;
            font-weight: 760;
            line-height: 1.2;
            padding: 0.48rem 0.8rem;
            text-decoration: none !important;
          }
          .match-info-link:hover {
            background: #1f2937;
            color: #ffffff !important;
            text-decoration: none !important;
          }
          @media (max-width: 560px) {
            .recommendation-footer {
              align-items: flex-start;
              flex-direction: column;
            }
          }
          .interest-avatar {
            align-items: center;
            aspect-ratio: 1;
            background: #f3f4f6;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            color: #4b5563;
            display: flex;
            font-size: 1.05rem;
            font-weight: 760;
            justify-content: center;
            min-height: 76px;
            width: 76px;
          }
          .interest-photo {
            aspect-ratio: 1;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            display: block;
            height: 76px;
            object-fit: cover;
            width: 76px;
          }
          .interest-title {
            background: #111827;
            border-radius: 6px;
            color: #ffffff !important;
            display: inline-block;
            font-size: 1.02rem !important;
            font-weight: 760 !important;
            line-height: 1.25 !important;
            margin: 0 0 0.35rem !important;
            padding: 0.18rem 0.45rem !important;
          }
          .interest-meta {
            color: #4b5563;
            font-size: 0.9rem !important;
            line-height: 1.45 !important;
            margin: 0 !important;
          }
          .interest-note {
            color: #6b7280;
            font-size: 0.82rem !important;
            line-height: 1.35 !important;
            margin-top: 0.3rem !important;
          }
          .profile-choice-row {
            display: grid;
            gap: 0.55rem;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            margin: 0.2rem 0 0.85rem;
          }
          .profile-choice {
            background: #111827;
            border-radius: 8px;
            color: #ffffff !important;
            min-width: 0;
            padding: 0.55rem 0.65rem;
          }
          .profile-choice-label {
            color: rgba(255, 255, 255, 0.76) !important;
            display: block;
            font-size: 0.76rem;
            font-weight: 760;
            line-height: 1.2;
            margin-bottom: 0.2rem;
          }
          .profile-choice-value {
            color: #ffffff !important;
            display: block;
            font-size: 0.92rem;
            font-weight: 720;
            line-height: 1.25;
            overflow-wrap: anywhere;
          }
          @media (max-width: 760px) {
            .profile-choice-row {
              grid-template-columns: 1fr;
            }
          }
          .interest-divider {
            border-top: 1px solid #eef2f7;
            margin: 0.9rem 0;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def selected_day_labels(workdays: list[int]) -> list[str]:
    return [label for label, value in DAY_LABELS.items() if value in workdays]


def parse_time_value(value: str, fallback: time) -> time:
    try:
        hour, minute = value.split(":")[:2]
        return time(int(hour), int(minute))
    except Exception:
        return fallback


def handle_google_oauth_callback(profile: dict) -> None:
    code = st.query_params.get("code")
    state = st.query_params.get("state")
    if not code:
        return

    expected_state = st.session_state.get("google_oauth_state")
    try:
        token_data = exchange_google_oauth_code(str(code), str(state or ""), expected_state)
        email = token_data.get("user_email") or get_stored_oauth_user_email()
        calendar_note = ""
        if oauth_token_has_calendar_management_scope():
            try:
                calendar = ensure_matchmate_calendar(profile.get("timezone", "Asia/Seoul"))
                calendar_note = f" · {calendar.get('summary', MATCHMATE_CALENDAR_SUMMARY)} 캘린더 사용"
            except Exception as exc:
                st.session_state["oauth_warning"] = f"전용 캘린더 생성 실패: {exc}"
        st.session_state["oauth_notice"] = f"Google 계정 연동 완료: {email or '연결됨'}{calendar_note}"
    except Exception as exc:
        st.session_state["oauth_error"] = f"Google 연동 실패: {exc}"
    finally:
        st.query_params.clear()


def google_email() -> str:
    email = get_stored_oauth_user_email()
    if email:
        return email
    try:
        return fetch_oauth_user_email()
    except Exception:
        return ""


def google_auth_url() -> str:
    st.session_state["google_oauth_state"] = new_oauth_state()
    return build_google_oauth_url(st.session_state["google_oauth_state"])


def google_connection_required_message(action: str) -> str:
    return (
        f"{action}하려면 먼저 Google 계정을 연동해주세요. "
        "상단의 Google 로그인/설정에서 Calendar와 Gmail 권한을 연결하면 됩니다."
    )


def display_timezone_name(timezone_name: str) -> str:
    label = TIMEZONE_DISPLAY_NAMES.get(timezone_name)
    if label:
        return f"{label} ({timezone_name})"
    return timezone_name


@st.cache_data(ttl=3600, show_spinner=False)
def location_records(cache_version: str = LOCATION_SEARCH_CACHE_VERSION) -> list[dict]:
    data = load_json(LOCATION_TIMEZONES_PATH, {"locations": []})
    records = []
    for index, raw_record in enumerate(data.get("locations", [])):
        region = str(raw_record.get("region") or "").strip()
        timezone_name = str(raw_record.get("timezone") or "").strip()
        if not region or not timezone_name:
            continue
        country = str(raw_record.get("country") or "").strip()
        keywords = [str(item) for item in raw_record.get("keywords", []) if item]
        search_parts = [region, country, timezone_name, *keywords]
        records.append(
            {
                "id": f"location-{index}",
                "index": index,
                "region": region,
                "country": country,
                "timezone": timezone_name,
                "keywords": keywords,
                "search_text": normalize(" ".join(search_parts)),
                "version": cache_version,
            }
        )
    return records


@st.cache_data(ttl=3600, show_spinner=False)
def timezone_records(cache_version: str = LOCATION_SEARCH_CACHE_VERSION) -> list[dict]:
    aliases_by_timezone: dict[str, set[str]] = {}
    for record in location_records(cache_version):
        aliases = aliases_by_timezone.setdefault(record["timezone"], set())
        aliases.update([record["region"], record.get("country", "")])
        aliases.update(record.get("keywords", []))

    try:
        all_timezones = sorted(available_timezones())
    except Exception:
        all_timezones = []
    ordered_timezones = list(PREFERRED_TIMEZONES)
    ordered_timezones.extend(tz for tz in all_timezones if tz not in PREFERRED_TIMEZONES)

    records = []
    seen = set()
    for timezone_name in ordered_timezones:
        if not timezone_name or timezone_name in seen:
            continue
        seen.add(timezone_name)
        label = display_timezone_name(timezone_name)
        aliases = sorted(item for item in aliases_by_timezone.get(timezone_name, set()) if item)
        region_name = timezone_name.split("/", 1)[0] if "/" in timezone_name else "Global"
        city_name = timezone_name.split("/")[-1].replace("_", " ")
        search_text = normalize(" ".join([timezone_name, label, region_name, city_name, *aliases]))
        records.append(
            {
                "timezone": timezone_name,
                "label": label,
                "subtitle": "IANA Time Zone DB",
                "search_text": search_text,
                "preferred": timezone_name in PREFERRED_TIMEZONES,
                "version": cache_version,
            }
        )
    return records


def find_location_record(value: str) -> dict | None:
    needle = normalize(value)
    if not needle:
        return None
    for record in location_records(LOCATION_SEARCH_CACHE_VERSION):
        exact_candidates = [record["region"], record["timezone"], *record.get("keywords", [])]
        if any(needle == normalize(candidate) for candidate in exact_candidates):
            return record
    for record in location_records(LOCATION_SEARCH_CACHE_VERSION):
        if needle in record["search_text"]:
            return record
    return None


def first_location_for_timezone(timezone_name: str) -> dict | None:
    for record in location_records(LOCATION_SEARCH_CACHE_VERSION):
        if record["timezone"] == timezone_name:
            return record
    return None


def initialize_profile_location_state(profile: dict) -> None:
    if REGION_STATE_KEY in st.session_state and TIMEZONE_STATE_KEY in st.session_state:
        return

    saved_timezone = str(profile.get("timezone") or DEFAULT_PROFILE["timezone"])
    saved_region = str(profile.get("region") or DEFAULT_PROFILE["region"])
    matched_location = find_location_record(saved_region) or first_location_for_timezone(saved_timezone)
    if REGION_STATE_KEY not in st.session_state:
        st.session_state[REGION_STATE_KEY] = matched_location["region"] if matched_location else saved_region
    if TIMEZONE_STATE_KEY not in st.session_state:
        st.session_state[TIMEZONE_STATE_KEY] = matched_location["timezone"] if matched_location else saved_timezone


def search_rank(needle: str, primary: str, search_text: str, preferred: bool = False) -> tuple[int, str]:
    primary_text = normalize(primary)
    if needle == primary_text:
        level = 0
    elif primary_text.startswith(needle):
        level = 1
    elif needle in primary_text:
        level = 2
    elif needle in search_text:
        level = 3
    else:
        level = 9
    return (level - (1 if preferred else 0), primary_text)


def location_search_options(searchterm: str) -> list[dict]:
    needle = normalize(searchterm)
    if not needle:
        return []
    matches = []
    for record in location_records(LOCATION_SEARCH_CACHE_VERSION):
        if needle not in record["search_text"]:
            continue
        matches.append(
            (
                (search_rank(needle, record["region"], record["search_text"]), record["index"]),
                {
                    "label": record["region"],
                    "value": record["region"],
                    "subtitle": f"{display_timezone_name(record['timezone'])} · {record['country']}",
                    "image": "",
                },
            )
        )
    return [option for _, option in sorted(matches, key=lambda item: item[0])[:10]]


def timezone_search_options(searchterm: str) -> list[dict]:
    needle = normalize(searchterm)
    if not needle:
        return []
    matches = []
    for record in timezone_records(LOCATION_SEARCH_CACHE_VERSION):
        if needle not in record["search_text"]:
            continue
        matches.append(
            (
                (
                    0 if record.get("preferred") else 1,
                    search_rank(needle, record["timezone"], record["search_text"]),
                ),
                {
                    "label": record["label"],
                    "value": record["timezone"],
                    "subtitle": record["subtitle"],
                    "image": "",
                },
            )
        )
    return [option for _, option in sorted(matches, key=lambda item: item[0])[:12]]


def render_profile_searchbox(
    label: str,
    placeholder: str,
    component_key: str,
    search_options,
    submit_option,
) -> None:
    state_key = f"{component_key}_state"
    handled_event_key = f"{component_key}_handled_sequence"
    if state_key not in st.session_state:
        st.session_state[state_key] = {"search": "", "options": []}

    component_state = st.session_state[state_key]
    event = photo_searchbox_component(
        label=label,
        placeholder=placeholder,
        searchterm=component_state.get("search", ""),
        options=component_state.get("options", []),
        key=component_key,
        default=None,
    )

    if not isinstance(event, dict):
        return

    interaction = event.get("interaction")
    value = str(event.get("value") or "").strip()
    sequence = event.get("sequence")
    if sequence is not None and st.session_state.get(handled_event_key) == sequence:
        interaction = ""
    elif sequence is not None:
        st.session_state[handled_event_key] = sequence

    if interaction == "search" and value != component_state.get("search", ""):
        component_state["search"] = value
        component_state["options"] = search_options(value)
        st.session_state[state_key] = component_state
        st.rerun()
    elif interaction == "submit" and value:
        submit_option(value)
        st.session_state[state_key] = {"search": "", "options": []}
        st.rerun()
    elif interaction == "reset":
        st.session_state[state_key] = {"search": "", "options": []}


def render_profile_location_timezone_picker(profile: dict) -> None:
    initialize_profile_location_state(profile)

    current_region = str(st.session_state.get(REGION_STATE_KEY) or profile.get("region") or DEFAULT_PROFILE["region"])
    current_timezone = str(
        st.session_state.get(TIMEZONE_STATE_KEY) or profile.get("timezone") or DEFAULT_PROFILE["timezone"]
    )
    st.markdown(
        f"""
        <div class="profile-choice-row">
          <div class="profile-choice">
            <span class="profile-choice-label">선택된 거주 지역</span>
            <span class="profile-choice-value">{html.escape(current_region)}</span>
          </div>
          <div class="profile-choice">
            <span class="profile-choice-label">선택된 시간대</span>
            <span class="profile-choice-value">{html.escape(display_timezone_name(current_timezone))}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    def submit_location(value: str) -> None:
        record = find_location_record(value)
        if not record:
            return
        st.session_state[REGION_STATE_KEY] = record["region"]
        st.session_state[TIMEZONE_STATE_KEY] = record["timezone"]

    def submit_timezone(value: str) -> None:
        valid_timezones = {record["timezone"] for record in timezone_records(LOCATION_SEARCH_CACHE_VERSION)}
        if value in valid_timezones:
            st.session_state[TIMEZONE_STATE_KEY] = value

    location_col, timezone_col = st.columns([1, 1])
    with location_col:
        render_profile_searchbox(
            "거주 지역 검색",
            "예: 서울, 뉴욕, London",
            "profile_location_searchbox",
            location_search_options,
            submit_location,
        )
    with timezone_col:
        render_profile_searchbox(
            "시간대 검색",
            "예: 한국, Asia/Seoul, PST",
            "profile_timezone_searchbox",
            timezone_search_options,
            submit_timezone,
        )


def ensure_default_matchmate_calendar(profile: dict) -> None:
    if not google_oauth_connected() or get_dedicated_calendar_id():
        return
    if not oauth_token_has_calendar_management_scope():
        return
    if st.session_state.get("default_calendar_checked"):
        return

    st.session_state["default_calendar_checked"] = True
    try:
        calendar = ensure_matchmate_calendar(profile.get("timezone", "Asia/Seoul"))
        st.session_state["oauth_notice"] = f"{calendar.get('summary', MATCHMATE_CALENDAR_SUMMARY)} 캘린더를 기본으로 사용합니다."
    except Exception as exc:
        st.session_state["oauth_warning"] = f"전용 캘린더 생성 실패: {exc}"


def render_google_menu(profile: dict) -> None:
    ensure_default_matchmate_calendar(profile)

    if google_oauth_connected():
        if st.button("Google 연결 해제", width="stretch"):
            disconnect_google_oauth()
            st.rerun()
    elif google_oauth_configured():
        st.link_button("Google 로그인", google_auth_url(), width="stretch", type="primary")
    else:
        st.button("Google 설정 필요", disabled=True, width="stretch")


def render_google_connect(profile: dict) -> None:
    ensure_default_matchmate_calendar(profile)

    if google_oauth_connected():
        if st.session_state.get("oauth_notice"):
            st.success(st.session_state.pop("oauth_notice"))
        if st.session_state.get("oauth_warning"):
            st.warning(st.session_state.pop("oauth_warning"))
        if st.session_state.get("oauth_error"):
            st.error(st.session_state.pop("oauth_error"))
        return

    st.markdown(
        """
        <div class="google-panel">
          <h2>Google 계정 연동</h2>
          <p>Google Calendar에 관심 경기 일정을 등록하고 Gmail로 관람 알림을 받을 수 있습니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([2.3, 1])
    with left:
        if google_oauth_connected():
            email = google_email()
            st.success(f"연동됨{': ' + email if email else ''}")
            if get_dedicated_calendar_id():
                st.caption(f"{MATCHMATE_CALENDAR_SUMMARY} 캘린더에 관심 경기 일정이 등록됩니다.")
        elif google_oauth_configured():
            st.info("Google 로그인을 누르면 Calendar와 Gmail 권한을 요청합니다.")
        else:
            st.warning("OAuth Client ID와 Secret을 저장하면 로그인 버튼이 활성화됩니다.")

    with right:
        if google_oauth_connected():
            if st.button("Google 연결 해제", width="stretch"):
                disconnect_google_oauth()
                st.rerun()
        elif google_oauth_configured():
            st.link_button("Google로 로그인", google_auth_url(), width="stretch", type="primary")
        else:
            st.button("Google로 로그인", disabled=True, width="stretch", type="primary")

    if st.session_state.get("oauth_notice"):
        st.success(st.session_state.pop("oauth_notice"))
    if st.session_state.get("oauth_warning"):
        st.warning(st.session_state.pop("oauth_warning"))
    if st.session_state.get("oauth_error"):
        st.error(st.session_state.pop("oauth_error"))

    if not google_oauth_configured() and not google_oauth_connected():
        with st.expander("Google OAuth 설정", expanded=False):
            with st.form("oauth_setup_form"):
                client_id = st.text_input("OAuth Client ID", value=get_oauth_client_id())
                client_secret = st.text_input("OAuth Client Secret", value="", type="password")
                redirect_uri = st.text_input("Redirect URI", value=get_oauth_redirect_uri())
                calendar_id = st.text_input("Calendar ID", value=get_oauth_config_value("GOOGLE_OAUTH_CALENDAR_ID", "primary"))
                submitted = st.form_submit_button("저장하고 로그인 버튼 켜기", width="stretch")
            if submitted:
                if not client_id or not client_secret:
                    st.error("Client ID와 Secret이 필요합니다.")
                else:
                    save_local_oauth_config(client_id, client_secret, redirect_uri, calendar_id)
                    st.success("저장했습니다. 이제 Google로 로그인할 수 있습니다.")
                    st.rerun()

    if google_oauth_configured() and not google_oauth_connected():
        with st.expander("OAuth 설정 초기화", expanded=False):
            if st.button("저장된 OAuth 설정 삭제", width="stretch"):
                clear_local_oauth_config()
                st.rerun()


def sync_calendar_candidates(candidates: list[dict], profile: dict, registry: dict) -> tuple[dict, list[dict]]:
    sync_summary = []
    for event in candidates:
        existing = registry.get(event["id"])
        existing_is_actual_google_event = bool(existing and existing.get("google_event_id"))
        can_skip_existing = (
            existing
            and existing.get("local_start") == event.get("local_start")
            and (existing_is_actual_google_event or not google_oauth_connected())
        )
        if can_skip_existing:
            sync_summary.append(
                {
                    "event_id": event["id"],
                    "title": event["title"],
                    "status": "duplicate_skipped",
                    "calendar_url": existing.get("calendar_url"),
                }
            )
            continue
        result = sync_calendar_event(event, profile, existing)
        registry[event["id"]] = registry_record(event, profile, result)
        sync_summary.append(
            {
                "event_id": event["id"],
                "title": event["title"],
                "status": result.status,
                "mode": result.mode,
                "calendar_url": result.calendar_url,
            }
        )
    return registry, sync_summary


def run_matchmate_scan(profile: dict, interests: dict, busy_blocks: list[dict], registry: dict) -> tuple[dict, dict]:
    start_date = date.today() - timedelta(days=7)
    end_date = date.today() + timedelta(days=21)
    sample_mode = config_bool("MATCHMATE_SAMPLE_MODE", config_bool("MATCHMATE_MOCK_MODE", False))
    client = SportsApiClient(DATA_PATH, sample_mode=sample_mode)
    fetch_result = client.fetch(interests, start_date, end_date)
    analysis = analyze_events(fetch_result.events, profile, interests, busy_blocks)
    registry, sync_summary = sync_calendar_candidates(analysis["calendar_candidates"], profile, registry)
    save_json(CALENDAR_REGISTRY_PATH, registry)

    viewing_email = generate_viewing_email(analysis, profile)
    newsletter = generate_weekly_newsletter(
        analysis,
        profile,
        interests,
        standings=fetch_result.standings,
        brackets=fetch_result.brackets,
    )
    VIEWING_EMAIL_PREVIEW_PATH.write_text(viewing_email, encoding="utf-8")
    NEWSLETTER_PREVIEW_PATH.write_text(newsletter, encoding="utf-8")

    last_run = {
        "source": fetch_result.source,
        "warnings": fetch_result.warnings,
        "analysis": analysis,
        "standings": fetch_result.standings,
        "brackets": fetch_result.brackets,
        "calendar_sync": sync_summary,
        "viewing_email": viewing_email,
        "newsletter": newsletter,
    }
    save_json(LAST_RUN_PATH, last_run)
    return last_run, registry


def event_start(event: dict) -> datetime:
    return parse_local_datetime(str(event["local_start"]), "Asia/Seoul")


def recommendation_score(event: dict) -> tuple[float, list[str]]:
    importance = float(event.get("importance", {}).get("score", 0))
    viewing = float(event.get("viewing", {}).get("score", 0))
    reasons = list(event.get("interest_reasons") or [])
    interest_bonus = 0.0
    if "관심 팀 경기" in reasons:
        interest_bonus += 32
    if "관심 국가대표 경기" in reasons:
        interest_bonus += 28
    if "관심 선수 관련 경기" in reasons:
        interest_bonus += 20
    if "관심 대회 경기" in reasons:
        interest_bonus += 10
    score = min(100.0, importance * 0.42 + viewing * 0.38 + interest_bonus)
    score_reasons = []
    if reasons:
        score_reasons.extend(reasons[:2])
    score_reasons.append(f"중요도 {importance:.0f}점")
    score_reasons.append(f"관람 가능성 {viewing:.0f}점")
    return score, score_reasons


def pick_recommended_match(analysis: dict, interests: dict) -> dict | None:
    scheduled = analysis.get("scheduled_events", [])
    interest_candidates = []
    important_candidates = []
    for event in scheduled:
        candidate = dict(event)
        current_reasons = interest_match_reasons(candidate, interests)
        candidate["interest_reasons"] = current_reasons
        candidate["is_interest_event"] = bool(current_reasons)
        is_interest = candidate["is_interest_event"]
        is_important = event.get("importance", {}).get("score", 0) >= 70
        score, reasons = recommendation_score(candidate)
        candidate["recommendation_score"] = score
        candidate["recommendation_reasons"] = reasons
        if is_interest:
            interest_candidates.append(candidate)
        elif is_important:
            important_candidates.append(candidate)

    candidates = interest_candidates or important_candidates
    if not candidates:
        return None

    strong = [event for event in candidates if event["recommendation_score"] >= 65]
    pool = strong or candidates
    return sorted(pool, key=lambda event: (event_start(event), -event["recommendation_score"]))[0]


def render_pills(items: list[str], terms: dict, category: str) -> None:
    if not items:
        st.markdown('<span class="empty-pill">등록된 항목 없음</span>', unsafe_allow_html=True)
        return
    pills = "".join(
        f'<span class="pill">{html.escape(display_term(item, terms, category))}</span>'
        for item in items
    )
    st.markdown(pills, unsafe_allow_html=True)


def render_interest_card(title: str, items: list[str], terms: dict, category: str) -> None:
    with st.container(border=True):
        st.markdown(f"#### {title}")
        render_pills(items, terms, category)


def save_interests_and_rerun(interests: dict) -> None:
    save_json(INTERESTS_PATH, interests)
    st.rerun()


def add_interest(interests: dict, category: str, value: str, terms: dict) -> None:
    canonical = canonical_terms([value], terms, category)
    if not canonical:
        return
    current = list(interests.get(category, []))
    for item in canonical:
        if item not in current:
            current.append(item)
    interests[category] = current
    save_interests_and_rerun(interests)


def remove_interest(interests: dict, category: str, value: str) -> None:
    interests[category] = [item for item in interests.get(category, []) if item != value]
    save_interests_and_rerun(interests)


def add_interest_without_rerun(interests: dict, category: str, value: str, terms: dict) -> bool:
    canonical = canonical_terms([value], terms, category)
    if not canonical:
        return False
    current = list(interests.get(category, []))
    added = False
    for item in canonical:
        if item not in current:
            current.append(item)
            added = True
    interests[category] = current
    if added:
        save_json(INTERESTS_PATH, interests)
    return added


def has_registered_interest_targets(interests: dict) -> bool:
    return any(interests.get(category) for category in ("teams", "players", "national_teams"))


def record_image_url(record: dict, category: str) -> str:
    if category in {"teams", "national_teams"}:
        return str(record.get("badge") or record.get("thumb") or "")
    return str(record.get("thumb") or record.get("badge") or "")


def record_subtitle(record: dict, category: str) -> str:
    if category in {"teams", "national_teams"}:
        parts = [record.get("sport"), record.get("league"), record.get("country")]
    else:
        parts = [record.get("sport"), record.get("team"), record.get("nationality") or record.get("description")]
    return " | ".join(str(part) for part in parts if part)


@st.cache_data(ttl=86400, show_spinner=False)
def cached_recommendation_team_search(category: str, team_name: str, cache_version: str = CATALOG_SEARCH_CACHE_VERSION) -> dict:
    client = SportsCatalogClient(CATALOG_CACHE_PATH)
    terms = load_terms(KOREAN_TERMS_PATH)
    record = client.find_team_logo_record(category, team_name, terms)
    return {
        "names": [record.get("name")] if record.get("name") else [],
        "records": [record] if record else [],
        "warning": "",
        "version": cache_version,
    }


def recommendation_team_categories(event: dict, team_name: str) -> list[str]:
    national_teams = {str(item) for item in event.get("national_teams") or []}
    league = str(event.get("league") or "")
    if team_name in national_teams or "World Cup" in league:
        return ["national_teams", "teams"]
    return ["teams", "national_teams"]


def recommendation_team_record(event: dict, team_name: str, terms: dict) -> tuple[str, dict]:
    categories = recommendation_team_categories(event, team_name)
    fallback = {"category": categories[0], "name": team_name}

    for category in categories:
        records = catalog_records_by_name(category)
        candidates = canonical_terms([team_name], terms, category) or [team_name]
        for candidate in candidates + [team_name]:
            record = records.get(candidate)
            if record and recommendation_record_has_trusted_image(record, category):
                return category, record
            if record:
                fallback = record

    if team_name.startswith("TBD"):
        return categories[0], fallback

    for category in categories:
        try:
            result = cached_recommendation_team_search(category, team_name, CATALOG_SEARCH_CACHE_VERSION)
        except Exception:
            continue
        for record in result.get("records", []):
            if record.get("name") and recommendation_record_has_trusted_image(record, category):
                return category, record
            if record.get("name"):
                fallback = record

    return str(fallback.get("category") or categories[0]), fallback


def recommendation_record_has_trusted_image(record: dict, category: str) -> bool:
    if not record_image_url(record, category):
        return False
    if category != "national_teams":
        image_kind = str(record.get("image_kind") or "")
        if image_kind == "club_logo":
            return image_url_looks_like_logo(record_image_url(record, category))
        if record.get("source") == "thesportsdb":
            return True
        return False
    if record.get("source") == "thesportsdb":
        return True
    image_kind = str(record.get("image_kind") or "")
    record_id = str(record.get("id") or "").casefold()
    return image_kind.startswith("national_team") or "national" in record_id


def recommendation_team_logo_html(event: dict, team_name: str, side_label: str, terms: dict) -> str:
    category, record = recommendation_team_record(event, team_name, terms)
    display_name = display_term(str(record.get("name") or team_name), terms, category)
    image_url = record_image_url(record, category)
    if image_url:
        visual = (
            f'<img class="matchup-logo" src="{html.escape(image_url, quote=True)}" '
            f'alt="{html.escape(display_name, quote=True)} 로고" />'
        )
    else:
        visual = f'<div class="matchup-logo-fallback">{html.escape(interest_initials(display_name))}</div>'
    return (
        '<div class="matchup-team">'
        f'<div class="matchup-side">{html.escape(side_label)}</div>'
        f'{visual}'
        f'<p class="matchup-name">{html.escape(display_name)}</p>'
        '</div>'
    )


def recommendation_matchup_html(event: dict, terms: dict) -> str:
    home = str(event.get("home_team") or "TBD")
    away = str(event.get("away_team") or "TBD")
    return (
        '<div class="recommendation-matchup">'
        f'{recommendation_team_logo_html(event, home, "홈", terms)}'
        '<div class="matchup-center">'
        f'<p class="recommendation-title">{html.escape(display_event_title(event, terms))}</p>'
        f'<p class="recommendation-meta">{html.escape(display_event_meta(event, terms))}</p>'
        '</div>'
        f'{recommendation_team_logo_html(event, away, "원정", terms)}'
        '</div>'
    )


def render_live_interest_search(
    label: str,
    category: str,
    placeholder: str,
    interests: dict,
    catalog: dict[str, list[str]],
    terms: dict,
) -> None:
    warning_key = f"{category}_search_warning"
    notice_key = f"{category}_added_notice"
    blocker_key = f"{category}_google_blocker"
    component_key = f"{category}_photo_searchbox"
    state_key = f"{component_key}_state"
    local_records = catalog_records_by_name(category)

    if state_key not in st.session_state:
        st.session_state[state_key] = {"search": "", "options": []}
    handled_event_key = f"{component_key}_handled_sequence"

    def search_options(searchterm: str) -> list[dict]:
        query = str(searchterm or "").strip()
        st.session_state[warning_key] = ""
        if not query:
            return []

        local_matches = local_catalog_matches(category, query, catalog, terms)
        external_names = []
        external_records = {}
        if len(query) >= 2:
            try:
                result = cached_external_catalog_search(category, query, CATALOG_SEARCH_CACHE_VERSION)
                external_names = result.get("names", [])
                external_records = {
                    str(record.get("name")): record
                    for record in result.get("records", [])
                    if record.get("name")
                }
                if not external_names and result.get("warning"):
                    st.session_state[warning_key] = result["warning"]
            except Exception as exc:
                st.session_state[warning_key] = f"TheSportsDB 검색 실패: {exc}"

        options = []
        seen = set()
        for item in local_matches + external_names:
            canonical = canonical_terms([item], terms, category)
            canonical_item = canonical[0] if canonical else item
            if canonical_item in seen:
                continue
            seen.add(canonical_item)
            record = external_records.get(canonical_item) or local_records.get(canonical_item) or {"category": category, "name": canonical_item}
            options.append(
                {
                    "label": display_term(canonical_item, terms, category),
                    "value": canonical_item,
                    "image": record_image_url(record, category),
                    "subtitle": record_subtitle(record, category),
                }
            )
        return options[:10]

    def submit_option(value: str) -> None:
        if not google_oauth_connected():
            st.session_state[blocker_key] = google_connection_required_message("관심 대상을 추가")
            return
        added = add_interest_without_rerun(interests, category, value, terms)
        label_text = display_term(value, terms, category)
        if added:
            st.session_state[notice_key] = f"추가됨: {label_text}"
        else:
            st.session_state[notice_key] = f"이미 등록됨: {label_text}"

    component_state = st.session_state[state_key]
    event = photo_searchbox_component(
        label=label,
        placeholder=placeholder,
        searchterm=component_state.get("search", ""),
        options=component_state.get("options", []),
        key=component_key,
        default=None,
    )

    if isinstance(event, dict):
        interaction = event.get("interaction")
        value = str(event.get("value") or "").strip()
        sequence = event.get("sequence")
        if sequence is not None and st.session_state.get(handled_event_key) == sequence:
            interaction = ""
        elif sequence is not None:
            st.session_state[handled_event_key] = sequence

        if interaction == "search" and value != component_state.get("search", ""):
            component_state["search"] = value
            component_state["options"] = search_options(value)
            st.session_state[state_key] = component_state
            st.rerun()
        elif interaction == "submit" and value:
            submit_option(value)
            st.session_state[state_key] = {"search": "", "options": []}
            component_state.clear()
            component_state.update(st.session_state[state_key])
        elif interaction == "reset":
            st.session_state[state_key] = {"search": "", "options": []}
            component_state.clear()
            component_state.update(st.session_state[state_key])

    if st.session_state.get(blocker_key):
        st.warning(st.session_state.pop(blocker_key))
    elif st.session_state.get(notice_key):
        st.caption(st.session_state.pop(notice_key))
    elif st.session_state.get(warning_key):
        st.caption(st.session_state[warning_key])


DETAIL_TRANSLATIONS = {
    "Soccer": "축구",
    "Football": "축구",
    "Baseball": "야구",
    "Basketball": "농구",
    "Tennis": "테니스",
    "Cricket": "크리켓",
    "Rugby": "럭비",
    "English Premier League": "프리미어리그",
    "Korean KBO League": "KBO 리그",
    "South Korean K League 2": "K리그2",
    "South Korea": "대한민국",
    "United States": "미국",
    "England": "잉글랜드",
    "Japan": "일본",
    "Jamaica": "자메이카",
    "France": "프랑스",
    "Nigeria": "나이지리아",
}


def stable_key(prefix: str, category: str, item: str) -> str:
    digest = hashlib.sha1(f"{category}:{item}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{category}_{digest}"


def display_detail_value(value: str, terms: dict, category: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw or raw.startswith("_No League") or raw.startswith("_Retired"):
        return ""
    if category:
        translated = display_term(raw, terms, category)
        if translated != raw:
            return translated
    for bucket in ("sports", "leagues", "teams", "national_teams", "venues"):
        translated = display_term(raw, terms, bucket)
        if translated != raw:
            return translated
    fallback = DETAIL_TRANSLATIONS.get(raw)
    if fallback and fallback != raw:
        return f"{fallback} ({raw})"
    return raw


def infer_interest_records_from_events(category: str) -> dict[str, dict]:
    sample = load_json(DATA_PATH, {"events": []})
    records: dict[str, dict] = {}
    for event in sample.get("events", []):
        if category == "teams":
            names = set(str(team) for team in event.get("teams", []) if team)
            for key in ("home_team", "away_team"):
                if event.get(key):
                    names.add(str(event[key]))
        elif category == "players":
            names = set(str(player) for player in event.get("players", []) if player)
        else:
            names = set(str(team) for team in event.get("national_teams", []) if team)

        for name in names:
            record = records.setdefault(
                name,
                {
                    "category": category,
                    "name": name,
                    "source": "sample",
                    "event_count": 0,
                },
            )
            record["event_count"] = int(record.get("event_count", 0)) + 1
            record.setdefault("sport", event.get("sport"))
            record.setdefault("league", event.get("league"))
            record.setdefault("venue", event.get("venue"))
            if category == "players":
                teams = [str(team) for team in event.get("teams", []) if team]
                if teams:
                    record.setdefault("team", " / ".join(teams[:2]))
            if category == "national_teams":
                record.setdefault("country", name)
            if event.get("status") != "completed" and not record.get("next_event"):
                record["next_event"] = f"{event.get('home_team')} vs {event.get('away_team')}"
    return records


def merged_interest_record(category: str, item: str, cached_records: dict[str, dict], inferred_records: dict[str, dict]) -> dict:
    record = {
        "category": category,
        "name": item,
    }
    for source_record in (inferred_records.get(item, {}), cached_records.get(item, {})):
        for key, value in source_record.items():
            if value not in (None, "", []):
                record[key] = value
    if category in {"teams", "national_teams"} and not recommendation_record_has_trusted_image(record, category):
        try:
            result = cached_recommendation_team_search(category, item, CATALOG_SEARCH_CACHE_VERSION)
            source_record = (result.get("records") or [{}])[0]
        except Exception:
            source_record = {}
        for key, value in source_record.items():
            if value not in (None, "", []):
                record[key] = value
    return record


def interest_initials(label: str) -> str:
    clean_label = html.unescape(label).split("(")[0].strip()
    parts = [part for part in clean_label.split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return "".join(part[0] for part in parts[:2]).upper()


def render_interest_visual(record: dict, label: str, category: str) -> None:
    image_url = record_image_url(record, category)
    if image_url:
        st.markdown(
            f'<img class="interest-photo" src="{html.escape(image_url, quote=True)}" alt="" />',
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        f'<div class="interest-avatar">{html.escape(interest_initials(label))}</div>',
        unsafe_allow_html=True,
    )


def interest_meta_lines(record: dict, category: str, terms: dict) -> tuple[str, str]:
    if category in {"teams", "national_teams"}:
        fields = [
            ("sport", "sports"),
            ("league", "leagues"),
            ("country", None),
            ("venue", "venues"),
        ]
    else:
        fields = [
            ("sport", "sports"),
            ("team", "teams"),
            ("nationality", None),
            ("description", None),
        ]

    parts = []
    for key, detail_category in fields:
        value = display_detail_value(str(record.get(key, "")), terms, detail_category)
        if value and value not in parts:
            parts.append(value)

    meta = " | ".join(parts[:4]) if parts else "추가 정보 준비 중"
    notes = []
    if record.get("event_count"):
        notes.append(f"연결된 일정 {record['event_count']}개")
    if record.get("next_event"):
        notes.append(f"다음 관련 경기: {record['next_event']}")
    source = str(record.get("source") or "")
    if source:
        source_label = {
            "thesportsdb": "TheSportsDB",
            "wikipedia": "Wikipedia",
            "sample": "내장 데이터",
        }.get(source, source)
        notes.append(f"정보 출처: {source_label}")
    return meta, " · ".join(notes)


def focused_interests_for_scan(interests: dict, category: str, values: list[str]) -> dict:
    return {
        "sports": interests.get("sports", DEFAULT_INTERESTS["sports"]),
        "teams": values if category == "teams" else [],
        "players": values if category == "players" else [],
        "national_teams": values if category == "national_teams" else [],
        "competitions": [],
        "include_important_unfollowed_matches": True,
    }


def scan_single_interest_item(
    category: str,
    item: str,
    profile: dict,
    interests: dict,
    busy_blocks: list[dict],
    calendar_registry: dict,
    terms: dict,
) -> None:
    if not google_oauth_connected():
        st.session_state["google_required_notice"] = google_connection_required_message("일정을 확인하고 캘린더에 등록")
        st.rerun()
    resolved_values, notes = catalog_client.resolve_values(category, [item], terms)
    values = resolved_values or [item]
    scan_interests = focused_interests_for_scan(interests, category, values)
    run_matchmate_scan(profile, scan_interests, busy_blocks, calendar_registry)
    label = display_term(values[0], terms, category)
    st.session_state["scan_notice"] = f"{label} 기준으로 일정을 확인하고 추천 경기를 갱신했습니다."
    if notes:
        st.session_state["scan_notes"] = "외부 DB 표준화: " + " / ".join(notes[:4])
    st.rerun()


def render_interest_item_card(
    item: str,
    category: str,
    interests: dict,
    terms: dict,
    cached_records: dict[str, dict],
    inferred_records: dict[str, dict],
    profile: dict,
    busy_blocks: list[dict],
    calendar_registry: dict,
) -> None:
    record = merged_interest_record(category, item, cached_records, inferred_records)
    label = display_term(item, terms, category)
    meta, note = interest_meta_lines(record, category, terms)
    image_col, info_col, action_col = st.columns([0.7, 3.1, 1.15], vertical_alignment="center")
    with image_col:
        render_interest_visual(record, label, category)
    with info_col:
        st.markdown(f'<p class="interest-title">{html.escape(label)}</p>', unsafe_allow_html=True)
        st.markdown(f'<p class="interest-meta">{html.escape(meta)}</p>', unsafe_allow_html=True)
        if note:
            st.markdown(f'<div class="interest-note">{html.escape(note)}</div>', unsafe_allow_html=True)
    with action_col:
        if st.button("일정 확인", key=stable_key("check", category, item), width="stretch"):
            scan_single_interest_item(category, item, profile, interests, busy_blocks, calendar_registry, terms)
        if st.button("삭제", key=stable_key("remove", category, item), width="stretch"):
            remove_interest(interests, category, item)


def render_interest_collection(
    title: str,
    category: str,
    interests: dict,
    terms: dict,
    profile: dict,
    busy_blocks: list[dict],
    calendar_registry: dict,
) -> None:
    with st.container(border=True):
        st.markdown(f'<p class="section-heading">{html.escape(title)}</p>', unsafe_allow_html=True)
        items = interests.get(category, [])
        if not items:
            st.markdown('<span class="empty-pill">등록된 항목 없음</span>', unsafe_allow_html=True)
            return

        cached_records = catalog_records_by_name(category)
        inferred_records = infer_interest_records_from_events(category)
        for index, item in enumerate(items):
            if index:
                st.markdown('<div class="interest-divider"></div>', unsafe_allow_html=True)
            render_interest_item_card(
                item,
                category,
                interests,
                terms,
                cached_records,
                inferred_records,
                profile,
                busy_blocks,
                calendar_registry,
            )


def render_recommendation(event: dict | None, registry: dict, terms: dict) -> None:
    if not event:
        with st.container(border=True):
            st.markdown('<p class="section-heading">추천 경기</p>', unsafe_allow_html=True)
            st.markdown('<p class="section-helper">아직 추천할 경기가 없습니다. 관심 목록에서 일정 확인을 누르면 추천 경기를 계산합니다.</p>', unsafe_allow_html=True)
        return

    with st.container(border=True):
        st.markdown('<p class="section-heading">추천 경기</p>', unsafe_allow_html=True)
        st.markdown(recommendation_matchup_html(event, terms), unsafe_allow_html=True)

        broadcast = ", ".join(event.get("broadcast") or ["중계 정보 확인 필요"])
        match_info_link = ""
        if event.get("external_url"):
            match_info_link = (
                f'<a class="match-info-link" href="{html.escape(event["external_url"], quote=True)}" '
                'target="_blank" rel="noopener noreferrer">경기 정보</a>'
            )
        st.markdown(
            f"""
            <div class="recommendation-footer">
              <div class="broadcast-line">
                <span class="broadcast-label">중계</span>
                <span class="broadcast-text">{html.escape(broadcast)}</span>
              </div>
              {match_info_link}
            </div>
            """,
            unsafe_allow_html=True,
        )


initialize_storage()
profile = load_json(PROFILE_PATH, DEFAULT_PROFILE)
interests = load_json(INTERESTS_PATH, DEFAULT_INTERESTS)
korean_terms = load_terms(KOREAN_TERMS_PATH)
canonicalized_interests = {
    **interests,
    "teams": canonical_terms(interests.get("teams", []), korean_terms, "teams"),
    "players": canonical_terms(interests.get("players", []), korean_terms, "players"),
    "national_teams": canonical_terms(interests.get("national_teams", []), korean_terms, "national_teams"),
}
if canonicalized_interests != interests:
    interests = canonicalized_interests
    save_json(INTERESTS_PATH, interests)
catalog_client = SportsCatalogClient(CATALOG_CACHE_PATH)
interest_catalog = load_interest_catalog(interests)
busy_blocks = load_json(BUSY_BLOCKS_PATH, [])
calendar_registry = load_json(CALENDAR_REGISTRY_PATH, {})
last_run = load_json(LAST_RUN_PATH, {})

st.set_page_config(page_title=APP_TITLE, layout="wide")
inject_styles()
handle_google_oauth_callback(profile)

header_left, header_right = st.columns([4.3, 1.2], vertical_alignment="top")
with header_left:
    st.markdown(
        f"""
        <div class="hero">
          <p class="hero-title">{APP_TITLE}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
with header_right:
    render_google_menu(profile)

render_google_connect(profile)

analysis = last_run.get("analysis", {})
recommended = pick_recommended_match(analysis, interests) if analysis and has_registered_interest_targets(interests) else None
render_recommendation(recommended, calendar_registry, korean_terms)
if st.session_state.get("google_required_notice"):
    st.warning(st.session_state.pop("google_required_notice"))
if st.session_state.get("scan_notice"):
    st.success(st.session_state.pop("scan_notice"))
if st.session_state.get("scan_notes"):
    st.caption(st.session_state.pop("scan_notes"))

main_left, main_right = st.columns([1.05, 1])

with main_left:
    with st.container(border=True):
        st.markdown('<p class="section-heading">사용자 정보</p>', unsafe_allow_html=True)
        render_profile_location_timezone_picker(profile)
        with st.form("profile_form", border=False):
            name = st.text_input("이름", value=profile.get("name", ""))

            day_col, work_col = st.columns([1, 1])
            with day_col:
                workday_labels = st.multiselect(
                    "업무 요일",
                    list(DAY_LABELS.keys()),
                    default=selected_day_labels(profile.get("workdays", [0, 1, 2, 3, 4])),
                )
            with work_col:
                work_start = st.time_input("업무 시작", value=parse_time_value(profile.get("work_start", "09:00"), time(9, 0)))
                work_end = st.time_input("업무 종료", value=parse_time_value(profile.get("work_end", "18:00"), time(18, 0)))

            sleep_col, wake_col = st.columns([1, 1])
            with sleep_col:
                sleep_start = st.time_input("취침 시간", value=parse_time_value(profile.get("sleep_start", "00:30"), time(0, 30)))
            with wake_col:
                wake_time = st.time_input("기상 시간", value=parse_time_value(profile.get("wake_time", "07:30"), time(7, 30)))

            saved_profile = st.form_submit_button("사용자 정보 저장", width="stretch")
            if saved_profile:
                if not google_oauth_connected():
                    st.warning(google_connection_required_message("사용자 정보를 저장"))
                else:
                    profile = {
                        "name": name,
                        "email": google_email() or profile.get("email", ""),
                        "timezone": str(st.session_state.get(TIMEZONE_STATE_KEY) or profile.get("timezone", "Asia/Seoul")),
                        "region": str(st.session_state.get(REGION_STATE_KEY) or profile.get("region", "서울, 대한민국")),
                        "workdays": [DAY_LABELS[label] for label in workday_labels],
                        "work_start": work_start.strftime("%H:%M"),
                        "work_end": work_end.strftime("%H:%M"),
                        "sleep_start": sleep_start.strftime("%H:%M"),
                        "wake_time": wake_time.strftime("%H:%M"),
                        "minimum_sleep_hours": float(profile.get("minimum_sleep_hours", 5.5)),
                        "calendar_reminder_minutes": int(profile.get("calendar_reminder_minutes", 60)),
                        "important_match_threshold": int(profile.get("important_match_threshold", 70)),
                    }
                    save_json(PROFILE_PATH, profile)
                    st.success("사용자 정보를 저장했습니다.")

with main_right:
    with st.container(border=True):
        st.markdown('<p class="section-heading">관심 대상 추가</p>', unsafe_allow_html=True)
        st.markdown(
            '<p class="section-helper">검색어를 입력하고 후보를 선택하면 아래 관심 목록에 바로 추가됩니다.</p>',
            unsafe_allow_html=True,
        )
        if not google_oauth_connected():
            st.info("Google 계정 연동 후 관심 대상을 추가할 수 있습니다.")

        render_live_interest_search(
            "관심 팀 검색",
            "teams",
            "예: 맨시티, Arsenal, LG 트윈스",
            interests,
            interest_catalog,
            korean_terms,
        )
        render_live_interest_search(
            "관심 선수 검색",
            "players",
            "예: 손흥민, Ohtani",
            interests,
            interest_catalog,
            korean_terms,
        )
        render_live_interest_search(
            "관심 국가대표 검색",
            "national_teams",
            "예: 대한민국, Japan",
            interests,
            interest_catalog,
            korean_terms,
        )

st.markdown('<p class="list-heading">관심 목록</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="section-helper">등록한 대상별로 일정 확인을 실행할 수 있습니다. 사진이 없으면 이니셜로 표시됩니다.</p>',
    unsafe_allow_html=True,
)

render_interest_collection(
    "관심 팀",
    "teams",
    interests,
    korean_terms,
    profile,
    busy_blocks,
    calendar_registry,
)
render_interest_collection(
    "관심 선수",
    "players",
    interests,
    korean_terms,
    profile,
    busy_blocks,
    calendar_registry,
)
render_interest_collection(
    "관심 국가대표",
    "national_teams",
    interests,
    korean_terms,
    profile,
    busy_blocks,
    calendar_registry,
)

if last_run.get("warnings"):
    st.caption(" · ".join(last_run["warnings"]))
