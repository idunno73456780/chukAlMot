from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from matchmate_agent import parse_local_datetime
from runtime_config import config_value


@dataclass
class CalendarSyncResult:
    mode: str
    status: str
    calendar_url: str | None = None
    google_event_id: str | None = None
    message: str = ""


def _calendar_datetime(value: str, timezone_name: str) -> str:
    parsed = parse_local_datetime(value, timezone_name)
    return parsed.strftime("%Y%m%dT%H%M%S")


def build_google_calendar_template_url(
    event: dict[str, Any],
    timezone_name: str,
    reminder_minutes: int = 60,
) -> str:
    title = f"[MatchMate] {event.get('title') or event.get('home_team', 'Match')}"
    start = _calendar_datetime(str(event["local_start"]), timezone_name)
    end = _calendar_datetime(str(event["local_end"]), timezone_name)
    details = [
        f"League: {event.get('league', '-')}",
        f"Sport: {event.get('sport', '-')}",
        f"Venue: {event.get('venue', '-')}",
        f"Broadcast: {', '.join(event.get('broadcast', []) or ['Check broadcast info'])}",
        f"MatchMate ID: {event.get('id', '-')}",
        "",
        f"Reminder suggestion: {reminder_minutes} minutes before kickoff.",
    ]
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{start}/{end}",
        "ctz": timezone_name,
        "details": "\n".join(details),
        "location": event.get("venue", ""),
    }
    return "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)


class GoogleCalendarClient:
    def __init__(self, calendar_id: str, credentials: Any):
        self.calendar_id = calendar_id
        self.credentials = credentials

    @classmethod
    def from_env(cls) -> "GoogleCalendarClient | None":
        calendar_id = config_value("GOOGLE_CALENDAR_ID", "primary")
        service_account_json = config_value("GOOGLE_SERVICE_ACCOUNT_JSON")
        service_account_file = config_value("GOOGLE_SERVICE_ACCOUNT_FILE")
        if not service_account_json and not service_account_file:
            return None

        try:
            from google.oauth2 import service_account
        except ImportError:
            return None

        scopes = ["https://www.googleapis.com/auth/calendar"]
        if service_account_json:
            info = json.loads(service_account_json)
            credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        else:
            credentials = service_account.Credentials.from_service_account_file(
                service_account_file,
                scopes=scopes,
            )
        return cls(calendar_id=calendar_id, credentials=credentials)

    def _service(self) -> Any:
        from googleapiclient.discovery import build

        return build("calendar", "v3", credentials=self.credentials, cache_discovery=False)

    def upsert_event(
        self,
        event: dict[str, Any],
        profile: dict[str, Any],
        existing_google_event_id: str | None = None,
    ) -> CalendarSyncResult:
        timezone_name = profile.get("timezone", "Asia/Seoul")
        reminder_minutes = int(profile.get("calendar_reminder_minutes", 60))
        start = parse_local_datetime(str(event["local_start"]), timezone_name)
        end = parse_local_datetime(str(event["local_end"]), timezone_name)
        body = {
            "summary": f"[MatchMate] {event.get('title')}",
            "location": event.get("venue", ""),
            "description": "\n".join(
                [
                    f"League: {event.get('league', '-')}",
                    f"Broadcast: {', '.join(event.get('broadcast', []) or ['Check broadcast info'])}",
                    f"Importance: {event.get('importance', {}).get('score', '-')}",
                    f"MatchMate ID: {event.get('id')}",
                ]
            ),
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "email", "minutes": reminder_minutes}],
            },
            "extendedProperties": {
                "private": {
                    "matchmate_event_id": str(event.get("id")),
                }
            },
        }
        service = self._service()
        if existing_google_event_id:
            response = (
                service.events()
                .update(calendarId=self.calendar_id, eventId=existing_google_event_id, body=body)
                .execute()
            )
            status = "updated"
        else:
            response = service.events().insert(calendarId=self.calendar_id, body=body).execute()
            status = "created"
        return CalendarSyncResult(
            mode="google_calendar_api",
            status=status,
            calendar_url=response.get("htmlLink"),
            google_event_id=response.get("id"),
            message=f"Google Calendar event {status}.",
        )


def sync_calendar_event(
    event: dict[str, Any],
    profile: dict[str, Any],
    registry_entry: dict[str, Any] | None = None,
) -> CalendarSyncResult:
    timezone_name = profile.get("timezone", "Asia/Seoul")
    reminder_minutes = int(profile.get("calendar_reminder_minutes", 60))
    template_url = build_google_calendar_template_url(event, timezone_name, reminder_minutes)

    try:
        from google_oauth_client import google_oauth_connected, upsert_oauth_calendar_event

        if google_oauth_connected():
            existing_id = registry_entry.get("google_event_id") if registry_entry else None
            response = upsert_oauth_calendar_event(event, profile, existing_id)
            return CalendarSyncResult(
                mode="google_oauth_calendar",
                status="updated" if existing_id else "created",
                calendar_url=response.get("htmlLink"),
                google_event_id=response.get("id"),
                message="Google OAuth Calendar event synced.",
            )
    except Exception:
        pass

    client = GoogleCalendarClient.from_env()
    if client is None:
        return CalendarSyncResult(
            mode="template_link",
            status="prepared",
            calendar_url=template_url,
            message="Google credentials are not configured, so a calendar template link was prepared.",
        )

    existing_id = None
    if registry_entry:
        existing_id = registry_entry.get("google_event_id")

    try:
        return client.upsert_event(event, profile, existing_google_event_id=existing_id)
    except Exception as exc:
        return CalendarSyncResult(
            mode="template_link",
            status="fallback",
            calendar_url=template_url,
            message=f"Calendar API failed, template link prepared instead: {exc}",
        )


def registry_record(
    event: dict[str, Any],
    profile: dict[str, Any],
    sync_result: CalendarSyncResult,
) -> dict[str, Any]:
    return {
        "event_id": event.get("id"),
        "title": event.get("title"),
        "league": event.get("league"),
        "local_start": event.get("local_start"),
        "local_end": event.get("local_end"),
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "reminder_minutes": int(profile.get("calendar_reminder_minutes", 60)),
        "mode": sync_result.mode,
        "status": sync_result.status,
        "calendar_url": sync_result.calendar_url,
        "google_event_id": sync_result.google_event_id,
        "message": sync_result.message,
    }


def stale_registry_cutoff(days: int = 30) -> datetime:
    return datetime.now() - timedelta(days=days)
