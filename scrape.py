import argparse
import hashlib
import json
from datetime import datetime

from google.cloud import firestore
from playwright.sync_api import Page, sync_playwright

from check_connection_firestore_db import get_firestore_client


URL = "https://www.set.or.th/en/market/stock-calendar/x-calendar"
YEAR = 2026
COLLECTION = "set_x_calendar_2026"


def fetch_month(page: Page, month: int):
    """Fetch the three feeds that populate the page's ul.days calendar."""
    return page.evaluate(
        """async ({year, month}) => {
            const base = `/api/set/stock-calendar/${year}/${month}`;
            const urls = {
                corporate: `${base}/x-calendar?symbols=&caTypes=&lang=en`,
                meetings: `${base}/meeting?symbols=&lang=en`,
                holidays: `${base}/holiday?lang=en`,
            };

            const entries = await Promise.all(
                Object.entries(urls).map(async ([key, url]) => {
                    const response = await fetch(url);
                    if (!response.ok) {
                        throw new Error(`${url} returned HTTP ${response.status}`);
                    }
                    return [key, await response.json()];
                })
            );
            return Object.fromEntries(entries);
        }""",
        {"year": YEAR, "month": month},
    )


def base_event(date_value, category, event_type, symbol, title, details):
    local_datetime = datetime.fromisoformat(date_value)
    return {
        "event_date": local_datetime,
        "event_date_iso": local_datetime.date().isoformat(),
        "year": local_datetime.year,
        "month": local_datetime.month,
        "category": category,
        "event_type": event_type,
        "symbol": symbol or None,
        "title": title,
        "details": details,
        "source": "Stock Exchange of Thailand X Calendar",
        "source_url": URL,
    }


def normalize_month(payload):
    events = []

    for day in payload["corporate"]:
        for event_group in day.get("types", []):
            event_type = event_group.get("type", "")
            for action in event_group.get("corporateActions", []):
                events.append(
                    base_event(
                        day["date"],
                        "corporate_action",
                        event_type,
                        action.get("symbol"),
                        action.get("name", ""),
                        action,
                    )
                )

    for day in payload["meetings"]:
        for meeting in day.get("meetings", []):
            events.append(
                base_event(
                    day["date"],
                    "shareholder_meeting",
                    meeting.get("type", "Meeting"),
                    meeting.get("symbol"),
                    meeting.get("name", ""),
                    meeting,
                )
            )

    for holiday in payload["holidays"]:
        description = holiday.get("description", "SET Holiday")
        events.append(
            base_event(
                holiday["date"],
                "set_holiday",
                "SET Holiday",
                None,
                description,
                holiday,
            )
        )

    return [event for event in events if event["year"] == YEAR]


def document_id(event):
    identity = {
        "date": event["event_date_iso"],
        "category": event["category"],
        "type": event["event_type"],
        "symbol": event["symbol"],
        "title": event["title"],
        "details": event["details"],
    }
    serialized = json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]


def save_events(db, events):
    written = 0
    for start in range(0, len(events), 500):
        batch = db.batch()
        for event in events[start : start + 500]:
            data = dict(event)
            data["scraped_at"] = firestore.SERVER_TIMESTAMP
            reference = db.collection(COLLECTION).document(document_id(event))
            batch.set(reference, data, merge=True)
            written += 1
        batch.commit()
    return written


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape the SET X Calendar for 2026 into Firestore."
    )
    parser.add_argument(
        "--month",
        type=int,
        choices=range(1, 13),
        help="Scrape one month only; the default scrapes all of 2026.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and print counts without writing to Firestore.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    months = [args.month] if args.month else list(range(1, 13))
    all_events = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        page.locator("ul.days").wait_for(state="attached", timeout=120_000)

        for month in months:
            payload = fetch_month(page, month)
            events = normalize_month(payload)
            all_events.extend(events)
            month_name = datetime(YEAR, month, 1).strftime("%B")
            print(f"{month_name}: scraped {len(events)} events")

        browser.close()

    unique_events = {document_id(event): event for event in all_events}
    events = list(unique_events.values())
    print(f"Total unique 2026 events: {len(events)}")

    if args.dry_run:
        print("Dry run complete; Firestore was not changed.")
        return

    db = get_firestore_client()
    written = save_events(db, events)
    print(f"Saved {written} events to Firestore collection '{COLLECTION}'.")


if __name__ == "__main__":
    main()
