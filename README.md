# XD Calendar Prototype

XD Calendar is a Python prototype that copies the Stock Exchange of Thailand (SET) X Calendar for 2026 into Google Calendar. It uses a headless browser to retrieve corporate actions, shareholder meetings, and SET holidays, stores normalized records in Firestore, then creates or updates color-coded all-day Google Calendar events.

## Structure

| File | Purpose |
| --- | --- |
| `main.py` | Runs Google Calendar OAuth, saves `token.json`, and creates one test event. |
| `scrape.py` | Retrieves all 12 months (or one month) from the SET page/API and writes deduplicated records to `set_x_calendar_2026`. |
| `check_connection_firestore_db.py` | Creates the Firestore client from `.env`; can also test the connection. |
| `insert_events.py` | Reads Firestore and idempotently creates, updates, or skips Google Calendar events. |
| `requirements.txt` | Python dependencies. |
| `.env` | Local Calendar and Firestore settings (not committed). |

## Tools, services, and constraints

- **Python 3** with the Google API clients and `python-dotenv`.
- **Playwright + Chromium** opens the SET page and calls its calendar endpoints in the page context. This depends on SET's current page/API structure and network availability; upstream changes can break the scraper.
- **Google Calendar API** uses an OAuth 2.0 Desktop client with the `calendar.events` scope. `client_secret_*.json` identifies the OAuth client and normally does not expire, but it can be revoked or rotated. `token.json` contains user access/refresh credentials: access tokens are short-lived and refreshed automatically, while refresh tokens can be revoked or expire. In particular, an external OAuth consent screen left in **Testing** issues a refresh token that normally expires after 7 days for this Calendar scope. Delete `token.json` and rerun `main.py` to authorize again. Never commit or share either credential file.
- **Cloud Firestore (Standard/Spark free quota)** stores the scraped data. The free allowance is one database per project, 1 GiB stored data, 50,000 document reads/day, 20,000 writes/day, 20,000 deletes/day, and 10 GiB outbound transfer/month; quotas reset daily around midnight Pacific time. Billing is required for usage or features outside the allowance. See [Firestore pricing](https://firebase.google.com/docs/firestore/pricing).
- **Firestore authentication is separate from Calendar OAuth.** Local runs use Google Application Default Credentials (ADC), typically created by `gcloud auth application-default login`. The signed-in identity needs access to the configured Firestore project.

## Process

```text
main.py -> browser OAuth -> token.json + one Calendar test event
scrape.py -> SET X Calendar -> normalize/deduplicate -> Firestore
insert_events.py -> Firestore -> create/update/skip Google Calendar events
```

The scraper uses deterministic Firestore document IDs and batches writes in groups of at most 500. The insertion script also derives stable Google event IDs and content hashes, so rerunning it updates changed events and skips unchanged ones instead of creating duplicates.

## Reproduce locally

### 1. Configure Google Cloud

In one Google Cloud project:

1. Enable the **Google Calendar API** and **Cloud Firestore API**.
2. Create a Firestore Standard database (the code supports `(default)` or a named database).
3. Configure the OAuth consent screen, add your account as a test user if the app is in Testing, and create an **OAuth client ID → Desktop app**.
4. Download the OAuth file into this directory as `client_secret_<id>.json`. The code expects exactly one matching file.
5. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), then authenticate Firestore locally:

```bash
gcloud auth application-default login
```

### 2. Install the project

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
playwright install chromium
```

Create `.env`:

```dotenv
SCOPES=["https://www.googleapis.com/auth/calendar.events"]
CALENDAR_ID=primary
FIRESTORE_PROJECT_ID=your-gcp-project-id
FIRESTORE_DATABASE_ID=(default)
```

Use `primary` for the signed-in user's main calendar, or supply another calendar ID to which that user has write access.

### 3. Run the pipeline

```bash
# Authorize Calendar, generate token.json, and create one test event.
python main.py

# Optional Firestore authentication/permission check.
python check_connection_firestore_db.py

# Preview, then scrape all of 2026 into Firestore.
python scrape.py --dry-run
python scrape.py

# Preview, then sync Firestore records to Google Calendar.
python insert_events.py --dry-run
python insert_events.py
```

For a smaller test, both pipeline scripts accept `--month 1` through `--month 12`; `insert_events.py` also accepts `--verbose`. The year and Firestore collection are currently fixed in code as `2026` and `set_x_calendar_2026`.

Keep `.env`, `client_secret_*.json`, `token.json`, service-account keys, and local ADC files out of source control. If any credential has already been exposed, revoke or rotate it rather than only deleting the file.
