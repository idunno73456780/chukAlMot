from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from email_notifier import send_markdown_email
from google_calendar_client import registry_record, sync_calendar_event
from matchmate_agent import (
    DEFAULT_INTERESTS,
    DEFAULT_PROFILE,
    analyze_events,
    generate_viewing_email,
    generate_weekly_newsletter,
    load_json,
    save_json,
)
from runtime_config import config_bool, config_int
from sports_api_client import SportsApiClient


DATA_PATH = ROOT / "data" / "sample_sports_events.json"
STORAGE = ROOT / "storage"
PROFILE_PATH = STORAGE / "user_profile.json"
INTERESTS_PATH = STORAGE / "sports_interests.json"
BUSY_BLOCKS_PATH = STORAGE / "busy_blocks.json"
CALENDAR_REGISTRY_PATH = STORAGE / "calendar_registry.json"
EMAIL_HISTORY_PATH = STORAGE / "email_history.json"
LAST_RUN_PATH = STORAGE / "last_run.json"
NEWSLETTER_PREVIEW_PATH = STORAGE / "latest_newsletter.md"
VIEWING_EMAIL_PREVIEW_PATH = STORAGE / "latest_viewing_email.md"


def google_oauth_is_connected() -> bool:
    try:
        from google_oauth_client import google_oauth_connected

        return google_oauth_connected()
    except Exception:
        return False


def notification_email(profile: dict) -> str:
    profile_email = str(profile.get("email", "")).strip()
    if profile_email:
        return profile_email
    try:
        from google_oauth_client import fetch_oauth_user_email, get_stored_oauth_user_email

        return get_stored_oauth_user_email() or fetch_oauth_user_email()
    except Exception:
        return ""


def main() -> int:
    profile = load_json(PROFILE_PATH, DEFAULT_PROFILE)
    interests = load_json(INTERESTS_PATH, DEFAULT_INTERESTS)
    busy_blocks = load_json(BUSY_BLOCKS_PATH, [])
    calendar_registry = load_json(CALENDAR_REGISTRY_PATH, {})
    email_history = load_json(EMAIL_HISTORY_PATH, [])

    start_date = date.today() - timedelta(days=config_int("MATCHMATE_LOOKBACK_DAYS", 7))
    end_date = date.today() + timedelta(days=config_int("MATCHMATE_LOOKAHEAD_DAYS", 14))
    sample_mode = config_bool("MATCHMATE_SAMPLE_MODE", config_bool("MATCHMATE_MOCK_MODE", False))

    client = SportsApiClient(DATA_PATH, sample_mode=sample_mode)
    fetch_result = client.fetch(interests, start_date, end_date)
    analysis = analyze_events(fetch_result.events, profile, interests, busy_blocks)

    sync_summary = []
    for event in analysis["calendar_candidates"]:
        existing = calendar_registry.get(event["id"])
        existing_is_actual_google_event = bool(existing and existing.get("google_event_id"))
        can_skip_existing = (
            existing
            and existing.get("local_start") == event.get("local_start")
            and (existing_is_actual_google_event or not google_oauth_is_connected())
        )
        if can_skip_existing:
            sync_summary.append(
                {
                    "event_id": event["id"],
                    "title": event["title"],
                    "status": "duplicate_skipped",
                    "message": "Duplicate calendar registration skipped.",
                    "calendar_url": existing.get("calendar_url"),
                }
            )
            continue
        result = sync_calendar_event(event, profile, existing)
        calendar_registry[event["id"]] = registry_record(event, profile, result)
        sync_summary.append(
            {
                "event_id": event["id"],
                "title": event["title"],
                "status": result.status,
                "mode": result.mode,
                "message": result.message,
                "calendar_url": result.calendar_url,
            }
        )

    save_json(CALENDAR_REGISTRY_PATH, calendar_registry)

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

    if config_bool("MATCHMATE_SEND_EMAIL", False):
        result = send_markdown_email(
            subject="[MatchMate] Weekly Sports Brief",
            body_markdown=newsletter,
            to_email=notification_email(profile),
            preview_path=NEWSLETTER_PREVIEW_PATH,
        )
        email_history.append(
            {
                "type": "scheduled_weekly_newsletter",
                "status": result.status,
                "message": result.message,
                "preview_path": result.preview_path,
            }
        )
        save_json(EMAIL_HISTORY_PATH, email_history)

    print("MatchMate scheduled check complete.")
    print(f"Source: {fetch_result.source}")
    print(f"Calendar candidates: {analysis['summary']['calendar_candidate_count']}")
    print(f"Important unfollowed: {analysis['summary']['important_unfollowed_count']}")
    for warning in fetch_result.warnings:
        print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
