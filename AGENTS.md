# PROJECT_CONTEXT.md - MatchMate Agent

## Project Summary

MatchMate Agent is a sports schedule assistant for the AI Agent onboarding assignment.

It helps users register teams, players, national teams, and leagues they care about, then checks sports schedules and decides which matches should be added to calendar workflows and which matches are realistically watchable based on the user's daily life.

## Agent Name

MatchMate Agent

## Core Concept

The agent:

- Stores user interests and daily schedule preferences.
- Retrieves sports match data from an API or mock sample data.
- Registers all interest-matching upcoming matches as calendar candidates.
- Creates Google Calendar events when credentials are configured.
- Falls back to Google Calendar template links when credentials are not configured.
- Prevents duplicate calendar registration.
- Scores match importance, including matches the user did not explicitly follow.
- Scores viewing feasibility from local timezone, work hours, sleep time, and next-day obligations.
- Generates viewing recommendation emails and weekly sports newsletters.

## MVP Tech Stack

- Python
- Streamlit
- TheSportsDB-compatible API client with mock fallback
- Google Calendar template links
- Optional Google Calendar API through service account
- Optional SMTP email
- JSON storage
- GitHub Actions scheduled execution

## Safety Rules

- Do not call external APIs on every Streamlit rerun.
- Use Streamlit forms and explicit buttons for external calls.
- Keep mock mode available for public demos.
- Do not commit API keys, SMTP passwords, service account JSON, OAuth tokens, or Streamlit secrets.
- Store runtime state under `storage/`.

## Main Files

- `app.py`: Streamlit UI.
- `matchmate_agent.py`: scoring, classification, newsletter generation, storage helpers.
- `sports_api_client.py`: sports API and mock data loading.
- `google_calendar_client.py`: Google Calendar API/template link integration.
- `email_notifier.py`: SMTP sending and markdown preview fallback.
- `scripts/check_sports_events.py`: scheduled/background execution entrypoint.
