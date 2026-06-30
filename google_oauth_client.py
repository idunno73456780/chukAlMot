from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


BASE_DIR = Path(__file__).resolve().parent
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
USERINFO_URI = "https://www.googleapis.com/oauth2/v2/userinfo"
TOKEN_PATH = BASE_DIR / "storage" / "google_oauth_token.json"
LOCAL_CONFIG_PATH = BASE_DIR / "storage" / "google_oauth_config.json"
MATCHMATE_CALENDAR_SUMMARY = "나이제축알못아니야"
MATCHMATE_CALENDAR_COLOR = "#2563EB"
MATCHMATE_EVENT_COLOR_ID = "9"
DEFAULT_TIMEZONE = "Asia/Seoul"

OAUTH_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]
CALENDAR_MANAGEMENT_SCOPE = "https://www.googleapis.com/auth/calendar"


class GoogleOAuthError(RuntimeError):
    pass


def _streamlit_secret(name: str) -> str:
    try:
        import streamlit as st

        value = st.secrets.get(name)
        return str(value) if value else ""
    except Exception:
        return ""


def _read_local_oauth_config() -> dict[str, Any]:
    if not LOCAL_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write_local_oauth_config(data: dict[str, Any]) -> None:
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_local_oauth_config(updates: dict[str, Any]) -> None:
    data = _read_local_oauth_config()
    data.update({key: value for key, value in updates.items() if value is not None})
    _write_local_oauth_config(data)


def save_local_oauth_config(client_id: str, client_secret: str, redirect_uri: str, calendar_id: str = "primary") -> None:
    previous = _read_local_oauth_config()
    _write_local_oauth_config(
        {
            "GOOGLE_OAUTH_CLIENT_ID": client_id.strip(),
            "GOOGLE_OAUTH_CLIENT_SECRET": client_secret.strip(),
            "GOOGLE_OAUTH_REDIRECT_URI": redirect_uri.strip() or "http://localhost:8503/",
            "GOOGLE_OAUTH_CALENDAR_ID": calendar_id.strip() or "primary",
            "GOOGLE_OAUTH_DEDICATED_CALENDAR_ID": previous.get("GOOGLE_OAUTH_DEDICATED_CALENDAR_ID", ""),
        }
    )


def clear_local_oauth_config() -> None:
    if LOCAL_CONFIG_PATH.exists():
        LOCAL_CONFIG_PATH.unlink()


def get_oauth_config_value(name: str, default: str = "") -> str:
    return os.getenv(name) or _streamlit_secret(name) or str(_read_local_oauth_config().get(name, "")) or default


def get_oauth_client_id() -> str:
    return get_oauth_config_value("GOOGLE_OAUTH_CLIENT_ID")


def get_oauth_client_secret() -> str:
    return get_oauth_config_value("GOOGLE_OAUTH_CLIENT_SECRET")


def get_oauth_redirect_uri() -> str:
    return get_oauth_config_value("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8503/")


def google_oauth_configured() -> bool:
    return bool(get_oauth_client_id() and get_oauth_client_secret())


def _token_path() -> Path:
    configured_path = get_oauth_config_value("GOOGLE_OAUTH_TOKEN_FILE")
    return Path(configured_path).expanduser() if configured_path else TOKEN_PATH


def _read_token_data() -> dict[str, Any]:
    env_token = os.getenv("GOOGLE_OAUTH_TOKEN_JSON", "").strip()
    if env_token:
        try:
            data = json.loads(env_token)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as exc:
            raise GoogleOAuthError("GOOGLE_OAUTH_TOKEN_JSON 형식이 올바르지 않습니다.") from exc

    path = _token_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write_token_data(data: dict[str, Any]) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _as_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _credentials_from_token_data(data: dict[str, Any]) -> Credentials:
    if not data.get("refresh_token") and not data.get("access_token"):
        raise GoogleOAuthError("저장된 Google OAuth 토큰이 없습니다.")
    return Credentials(
        token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_uri=TOKEN_URI,
        client_id=data.get("client_id") or get_oauth_client_id(),
        client_secret=data.get("client_secret") or get_oauth_client_secret(),
        scopes=data.get("scopes") or OAUTH_SCOPES,
        expiry=_parse_expiry(data.get("expiry")),
    )


def _token_data_from_credentials(credentials: Credentials, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    expiry = _as_naive_utc(credentials.expiry)
    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token or previous.get("refresh_token"),
        "token_uri": TOKEN_URI,
        "client_id": credentials.client_id or get_oauth_client_id(),
        "client_secret": credentials.client_secret or get_oauth_client_secret(),
        "scopes": list(credentials.scopes or OAUTH_SCOPES),
        "expiry": expiry.isoformat() if expiry else previous.get("expiry"),
        "user_email": previous.get("user_email", ""),
    }


def google_oauth_connected() -> bool:
    try:
        data = _read_token_data()
        return bool(data.get("refresh_token") or data.get("access_token"))
    except GoogleOAuthError:
        return False


def oauth_token_has_calendar_management_scope() -> bool:
    try:
        scopes = set(_read_token_data().get("scopes") or [])
    except GoogleOAuthError:
        return False
    return CALENDAR_MANAGEMENT_SCOPE in scopes


def new_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def build_google_oauth_url(state: str) -> str:
    if not google_oauth_configured():
        raise GoogleOAuthError("GOOGLE_OAUTH_CLIENT_ID와 GOOGLE_OAUTH_CLIENT_SECRET이 필요합니다.")
    params = {
        "client_id": get_oauth_client_id(),
        "redirect_uri": get_oauth_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URI}?{urlencode(params)}"


def exchange_google_oauth_code(code: str, state: str, expected_state: str | None = None) -> dict[str, Any]:
    if expected_state and state != expected_state:
        raise GoogleOAuthError("OAuth state가 일치하지 않습니다. 다시 로그인해 주세요.")
    if not google_oauth_configured():
        raise GoogleOAuthError("GOOGLE_OAUTH_CLIENT_ID와 GOOGLE_OAUTH_CLIENT_SECRET이 필요합니다.")

    response = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": get_oauth_client_id(),
            "client_secret": get_oauth_client_secret(),
            "redirect_uri": get_oauth_redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if not response.ok:
        raise GoogleOAuthError(f"Google OAuth 토큰 교환 실패: {response.text}")

    token = response.json()
    expiry = _as_naive_utc(datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in", 3600))))
    data = {
        "access_token": token.get("access_token"),
        "refresh_token": token.get("refresh_token"),
        "token_uri": TOKEN_URI,
        "client_id": get_oauth_client_id(),
        "client_secret": get_oauth_client_secret(),
        "scopes": token.get("scope", " ".join(OAUTH_SCOPES)).split(),
        "expiry": expiry.isoformat(),
        "user_email": "",
    }
    _write_token_data(data)
    email = fetch_oauth_user_email()
    data["user_email"] = email
    _write_token_data(data)
    return data


def get_oauth_credentials() -> Credentials:
    if not google_oauth_configured():
        raise GoogleOAuthError("GOOGLE_OAUTH_CLIENT_ID와 GOOGLE_OAUTH_CLIENT_SECRET이 필요합니다.")
    data = _read_token_data()
    credentials = _credentials_from_token_data(data)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _write_token_data(_token_data_from_credentials(credentials, previous=data))
    if not credentials.valid and not credentials.token:
        raise GoogleOAuthError("Google OAuth 토큰이 유효하지 않습니다. 다시 로그인해 주세요.")
    return credentials


def fetch_oauth_user_email() -> str:
    credentials = get_oauth_credentials()
    response = requests.get(
        USERINFO_URI,
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=15,
    )
    if not response.ok:
        return ""
    email = response.json().get("email", "")
    if email:
        data = _read_token_data()
        data["user_email"] = email
        _write_token_data(data)
    return email


def get_stored_oauth_user_email() -> str:
    return str(_read_token_data().get("user_email", ""))


def get_dedicated_calendar_id() -> str:
    return get_oauth_config_value("GOOGLE_OAUTH_DEDICATED_CALENDAR_ID", "")


def _save_dedicated_calendar_id(calendar_id: str) -> None:
    _update_local_oauth_config(
        {
            "GOOGLE_OAUTH_DEDICATED_CALENDAR_ID": calendar_id,
            "GOOGLE_OAUTH_CALENDAR_ID": calendar_id,
        }
    )


def _style_matchmate_calendar(service: Any, calendar_id: str) -> None:
    try:
        service.calendarList().patch(
            calendarId=calendar_id,
            colorRgbFormat=True,
            body={
                "backgroundColor": MATCHMATE_CALENDAR_COLOR,
                "foregroundColor": "#ffffff",
                "selected": True,
            },
        ).execute()
    except HttpError:
        pass


def _find_matchmate_calendar(service: Any) -> dict[str, Any] | None:
    page_token = None
    while True:
        response = service.calendarList().list(minAccessRole="owner", pageToken=page_token).execute()
        for item in response.get("items", []):
            if item.get("summary") == MATCHMATE_CALENDAR_SUMMARY and not item.get("primary"):
                return item
        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def ensure_matchmate_calendar(timezone_name: str = DEFAULT_TIMEZONE) -> dict[str, Any]:
    if not oauth_token_has_calendar_management_scope():
        raise GoogleOAuthError("전용 캘린더 생성을 위해 Google 권한 재승인이 필요합니다.")

    credentials = get_oauth_credentials()
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    calendar_id = get_dedicated_calendar_id()

    if calendar_id:
        try:
            calendar = service.calendars().get(calendarId=calendar_id).execute()
            _style_matchmate_calendar(service, calendar_id)
            return calendar
        except HttpError:
            calendar_id = ""

    existing = _find_matchmate_calendar(service)
    if existing:
        calendar_id = existing["id"]
    else:
        created = service.calendars().insert(
            body={"summary": MATCHMATE_CALENDAR_SUMMARY, "timeZone": timezone_name}
        ).execute()
        calendar_id = created["id"]

    _style_matchmate_calendar(service, calendar_id)
    _save_dedicated_calendar_id(calendar_id)
    return service.calendars().get(calendarId=calendar_id).execute()


def disconnect_google_oauth() -> None:
    data = _read_token_data()
    token = data.get("refresh_token") or data.get("access_token")
    if token:
        try:
            requests.post(REVOKE_URI, params={"token": token}, timeout=10)
        except requests.RequestException:
            pass
    path = _token_path()
    if path.exists():
        path.unlink()


def _match_event_body(event: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    timezone_name = profile.get("timezone", DEFAULT_TIMEZONE)
    reminder_minutes = int(profile.get("calendar_reminder_minutes", 60))
    description = "\n".join(
        [
            f"League: {event.get('league', '-')}",
            f"Sport: {event.get('sport', '-')}",
            f"Broadcast: {', '.join(event.get('broadcast', []) or ['Check broadcast info'])}",
            f"Importance: {event.get('importance', {}).get('score', '-')}",
            f"Viewing: {event.get('viewing', {}).get('bucket', '-')}",
            f"MatchMate ID: {event.get('id', '-')}",
        ]
    )
    return {
        "summary": f"[MatchMate] {event.get('title')}",
        "location": event.get("venue", ""),
        "description": description,
        "colorId": get_oauth_config_value("GOOGLE_OAUTH_EVENT_COLOR_ID", MATCHMATE_EVENT_COLOR_ID),
        "start": {"dateTime": event["local_start"], "timeZone": timezone_name},
        "end": {"dateTime": event["local_end"], "timeZone": timezone_name},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": reminder_minutes},
                {"method": "popup", "minutes": reminder_minutes},
            ],
        },
        "extendedProperties": {"private": {"matchmate_event_id": str(event.get("id"))}},
    }


def upsert_oauth_calendar_event(
    event: dict[str, Any],
    profile: dict[str, Any],
    existing_google_event_id: str | None = None,
) -> dict[str, Any]:
    credentials = get_oauth_credentials()
    calendar = ensure_matchmate_calendar(profile.get("timezone", DEFAULT_TIMEZONE))
    calendar_id = calendar.get("id") or get_dedicated_calendar_id() or "primary"
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    body = _match_event_body(event, profile)
    if existing_google_event_id:
        return service.events().update(
            calendarId=calendar_id,
            eventId=existing_google_event_id,
            body=body,
        ).execute()
    return service.events().insert(calendarId=calendar_id, body=body).execute()


def send_oauth_markdown_email(to_email: str, subject: str, body_markdown: str) -> dict[str, Any]:
    if not to_email:
        raise GoogleOAuthError("수신 이메일이 비어 있습니다.")
    credentials = get_oauth_credentials()
    from_email = get_stored_oauth_user_email() or fetch_oauth_user_email() or "me"
    message = EmailMessage()
    message["To"] = to_email
    message["From"] = from_email
    message["Subject"] = subject
    message.set_content(body_markdown)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()
