import json
import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.json"


def get_settings():
    load_dotenv(BASE_DIR / ".env")

    calendar_id = os.getenv("CALENDAR_ID", "primary").strip('"\'')
    scopes_value = os.getenv(
        "SCOPES", '["https://www.googleapis.com/auth/calendar"]'
    )
    try:
        scopes = json.loads(scopes_value)
    except json.JSONDecodeError as error:
        raise ValueError("SCOPES in .env must be a JSON list of strings") from error

    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise ValueError("SCOPES in .env must be a JSON list of strings")

    return calendar_id, scopes


def find_client_secret_file():
    files = list(BASE_DIR.glob("client_secret_*.json"))
    if len(files) != 1:
        raise FileNotFoundError(
            "Expected exactly one client_secret_*.json file in the project directory"
        )
    return files[0]


def token_covers_scopes(credentials, scopes):
    granted = set(credentials.scopes or [])
    return set(scopes).issubset(granted)


def authorize(scopes):
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, scopes)

    needs_consent = (
        not credentials
        or not token_covers_scopes(credentials, scopes)
        or not credentials.valid
    )
    if needs_consent:
        if (
            credentials
            and credentials.valid
            and not token_covers_scopes(credentials, scopes)
        ):
            credentials = None
        if credentials and credentials.expired and credentials.refresh_token:
            if token_covers_scopes(credentials, scopes):
                credentials.refresh(Request())
            else:
                credentials = None
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                find_client_secret_file(), scopes
            )
            credentials = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(credentials.to_json())
        TOKEN_FILE.chmod(0o600)

    return credentials


def main():
    calendar_id, scopes = get_settings()
    credentials = authorize(scopes)
    calendar = build("calendar", "v3", credentials=credentials)

    start_date = date.today()
    event = {
        "summary": "XD Calendar test event",
        "description": "Test event created by the XD Calendar Python script.",
        "start": {"date": start_date.isoformat()},
        # Google Calendar uses an exclusive end date for all-day events.
        "end": {"date": (start_date + timedelta(days=1)).isoformat()},
    }

    created_event = (
        calendar.events()
        .insert(calendarId=calendar_id, body=event)
        .execute()
    )

    print(f"Created all-day event: {created_event.get('summary')}")
    print(created_event.get("htmlLink", "No event link returned"))


if __name__ == "__main__":
    main()
