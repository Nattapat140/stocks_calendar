import argparse
import hashlib
import html
import json
import sys
from datetime import date, datetime, timedelta

from google.cloud.firestore_v1.base_query import FieldFilter
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from check_connection_firestore_db import get_firestore_client
from main import BASE_DIR, authorize, get_settings
from picker.server import prompt_selection


YEAR = 2026
COLLECTION = "set_x_calendar_2026"
OWNED_CALENDAR_NAME = "SET X Calendar 2026"
OWNED_CALENDAR_MARKER = "xd-calendar:set_x_calendar_2026"
OWNED_CALENDAR_ID_FILE = BASE_DIR / ".set_calendar_id"

COLOR_IDS = {
    "Meeting": "9",
    "XM": "9",
    "XD": "10",
    "XR": "2",
    "XW": "5",
    "XT": "6",
    "XE": "3",
    "XN": "3",
    "XB": "11",
    "SET Holiday": "8",
}

EMOJIS = {
    "Meeting": "🏦",
    "XM": "🔵",
    "XD": "🟢",
    "XR": "⚪",
    "XW": "🟡",
    "XT": "🟠",
    "XE": "🟣",
    "XN": "🟤",
    "XB": "🔴",
    "SET Holiday": "⚫",
}

EVENT_TYPE_ORDER = (
    "XD",
    "XR",
    "XW",
    "XT",
    "XE",
    "XN",
    "XB",
    "XM",
    "Meeting",
    "SET Holiday",
)


def save_owned_calendar_id(calendar_id):
    OWNED_CALENDAR_ID_FILE.write_text(calendar_id, encoding="utf-8")


def load_saved_owned_calendar_id():
    if not OWNED_CALENDAR_ID_FILE.exists():
        return None
    calendar_id = OWNED_CALENDAR_ID_FILE.read_text(encoding="utf-8").strip()
    return calendar_id or None


def calendar_exists(calendar, calendar_id):
    try:
        calendar.calendars().get(calendarId=calendar_id).execute(num_retries=3)
        return True
    except HttpError as error:
        status = getattr(error, "status_code", None) or int(error.resp.status)
        if status == 404:
            return False
        raise


def find_marked_owned_calendar(calendar):
    page_token = None
    while True:
        response = (
            calendar.calendarList()
            .list(maxResults=250, pageToken=page_token)
            .execute(num_retries=3)
        )
        for item in response.get("items", []):
            description = item.get("description") or ""
            if OWNED_CALENDAR_MARKER in description:
                return item["id"]
        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def create_owned_calendar(calendar):
    created = (
        calendar.calendars()
        .insert(
            body={
                "summary": OWNED_CALENDAR_NAME,
                "description": (
                    "SET X Calendar events managed by the XD Calendar script. "
                    f"{OWNED_CALENDAR_MARKER}"
                ),
                "timeZone": "Asia/Bangkok",
            }
        )
        .execute(num_retries=3)
    )
    return created["id"]


def resolve_calendar_id(calendar, configured_id, calendar_target, dry_run=False):
    """Return the Google calendar id and whether a real run would create it."""
    if calendar_target == "configured":
        return configured_id, False

    saved_id = load_saved_owned_calendar_id()
    if saved_id and calendar_exists(calendar, saved_id):
        return saved_id, False

    marked_id = find_marked_owned_calendar(calendar)
    if marked_id:
        save_owned_calendar_id(marked_id)
        return marked_id, False

    if dry_run:
        print(
            "Dry run: My SET calendar does not exist yet. "
            "A real run would create SET X Calendar 2026 once and reuse it later."
        )
        return None, True

    calendar_id = create_owned_calendar(calendar)
    save_owned_calendar_id(calendar_id)
    print(f"Created calendar '{OWNED_CALENDAR_NAME}': {calendar_id}")
    return calendar_id, False


def parse_args():
    parser = argparse.ArgumentParser(
        description="Insert 2026 SET events into Google Calendar."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read Firestore and Calendar, but do not create, update, or delete events.",
    )
    parser.add_argument(
        "--month",
        type=int,
        choices=range(1, 13),
        help="Process one month only; the default processes all of 2026.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every created, updated, skipped, or deleted event.",
    )
    return parser.parse_args()


def semantic_identity(record):
    return (
        record.get("event_date_iso", ""),
        record.get("category", ""),
        record.get("event_type", ""),
        record.get("symbol") or record.get("title") or "",
    )


def duplicate_discriminator(record):
    details = record.get("details") or {}
    event_type = record.get("event_type")
    if event_type == "XD":
        return f"dividendType={details.get('dividendType') or '<none>'}"
    if event_type == "XM":
        return f"meetingDate={details.get('meetingDate') or '<none>'}"
    if event_type == "XW":
        return f"ratio={details.get('ratio') or '<none>'}"
    return None


def load_2026_events(db, month=None):
    query = db.collection(COLLECTION).where(
        filter=FieldFilter("year", "==", YEAR)
    )
    records = [(document.id, document.to_dict()) for document in query.stream()]

    groups = {}
    for _, record in records:
        identity = semantic_identity(record)
        groups[identity] = groups.get(identity, 0) + 1

    for _, record in records:
        if groups[semantic_identity(record)] > 1:
            discriminator = duplicate_discriminator(record)
            if discriminator is None:
                raise ValueError(
                    "Duplicate SET records need a stable discriminator: "
                    f"{semantic_identity(record)}"
                )
            record["_idempotency_discriminator"] = str(discriminator)

    if month is not None:
        records = [item for item in records if item[1].get("month") == month]

    return sorted(
        records,
        key=lambda item: (
            item[1].get("event_date_iso", ""),
            item[1].get("category", ""),
            item[1].get("event_type", ""),
            item[1].get("symbol") or "",
            item[0],
        ),
    )


def format_date(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value.strftime("%d %b %Y")
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)

    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        return parsed.strftime("%d %b %Y")
    return parsed.strftime("%d %b %Y, %H:%M")


def display(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def date_range(start, end):
    if not start and not end:
        return "-"
    return f"{format_date(start)} - {format_date(end)}"


def first_value(details, *keys):
    for key in keys:
        value = details.get(key)
        if value is not None and value != "":
            return value
    return None


def description_lines(record):
    details = record.get("details") or {}
    category = record.get("category")
    event_type = record.get("event_type", "")
    company_name = first_value(details, "name") or record.get("title")

    if category == "set_holiday":
        return [("Description", first_value(details, "description") or record.get("title"))]

    if category == "shareholder_meeting" or event_type == "XM":
        return [
            ("Full Company Name", company_name),
            ("Book Closing Date", format_date(details.get("bookCloseDate"))),
            ("Record Date", format_date(details.get("recordDate"))),
            ("Meeting Date", format_date(details.get("meetingDate"))),
            ("Agenda", details.get("agenda")),
            ("Type of Meeting", details.get("meetingType")),
            ("Venue / Channel for Inquiry", first_value(details, "venue", "inquiryDate")),
        ]

    if event_type == "XD":
        dividend = first_value(
            details,
            "dividendPayment",
            "tentativeDividend",
            "dividend",
            "ratio",
        )
        if dividend not in (None, "-") and details.get("currency"):
            dividend = f"{dividend} {details['currency']}"
        return [
            ("Full Company Name", company_name),
            ("X-Date", format_date(details.get("xdate"))),
            ("Book Closing Date", format_date(details.get("bookCloseDate"))),
            ("Record Date", format_date(details.get("recordDate"))),
            ("Payment Date", format_date(first_value(details, "paymentDate", "approximatePaymentDate"))),
            ("Type", details.get("dividendType")),
            ("Dividend (Baht/Shares)", dividend),
            ("Operation Period", date_range(details.get("beginOperation"), details.get("endOperation"))),
            ("Source of Dividend", details.get("sourceOfDividend")),
        ]

    common = [
        ("Full Company Name", company_name),
        ("X-Date", format_date(details.get("xdate"))),
        ("Book Closing Date", format_date(details.get("bookCloseDate"))),
        ("Record Date", format_date(details.get("recordDate"))),
    ]

    if event_type in {"XR", "XW", "XT"}:
        return common + [
            ("Rights For", details.get("rightsFor")),
            ("Ratio", details.get("ratio")),
            ("Subscription Period", date_range(details.get("beginSubscription"), details.get("endSubscription"))),
            ("Price", details.get("price")),
            ("Condition", details.get("condition")),
        ]

    if event_type == "XE":
        return common + [
            ("Exercise Period", date_range(first_value(details, "beginExercise", "exerciseBeginDate"), first_value(details, "endExercise", "exerciseEndDate"))),
            ("Exercise Date", format_date(details.get("exerciseDate"))),
            ("Exercise Ratio", first_value(details, "exerciseRatio", "ratio")),
            ("Exercise Price", first_value(details, "exercisePrice", "price")),
        ]

    if event_type == "XN":
        amount = first_value(details, "returnAmount", "paymentDetail")
        if amount is not None and details.get("currency"):
            amount = f"{amount} {details['currency']}"
        return common + [
            ("Payment Date", format_date(details.get("paymentDate"))),
            ("Capital Return", amount),
        ]

    if event_type == "XB":
        price = details.get("price")
        if price is not None and details.get("currency"):
            price = f"{price} {details['currency']}"
        return common + [
            ("Benefit Type", details.get("benefitType")),
            ("Security Type", details.get("securityType")),
            ("Ratio", details.get("ratio")),
            ("Price", price),
            ("Payment Detail", details.get("paymentDetail")),
        ]

    return common


def event_kind(record):
    if record.get("category") == "shareholder_meeting":
        return "Meeting"
    return record.get("event_type") or "SET Event"


def selection_options(records):
    symbol_counts = {}
    type_counts = {kind: 0 for kind in EVENT_TYPE_ORDER}
    for _, record in records:
        kind = event_kind(record)
        type_counts[kind] = type_counts.get(kind, 0) + 1
        if record.get("category") == "set_holiday":
            continue
        symbol = (record.get("symbol") or "").strip()
        if symbol:
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

    extra_types = sorted(kind for kind in type_counts if kind not in EVENT_TYPE_ORDER)
    return {
        "symbols": [
            {"symbol": symbol, "count": count}
            for symbol, count in sorted(symbol_counts.items())
        ],
        "event_types": [
            {"type": kind, "count": type_counts.get(kind, 0)}
            for kind in (*EVENT_TYPE_ORDER, *extra_types)
        ],
    }


def record_matches(record, symbols, event_types):
    """Company rows need both a selected ticker and event type. Holidays need only the type."""
    if record.get("category") == "set_holiday":
        return "SET Holiday" in event_types
    symbol = (record.get("symbol") or "").strip()
    return symbol in symbols and event_kind(record) in event_types


def event_summary(record):
    kind = event_kind(record)
    emoji = EMOJIS.get(kind, "⚪")
    if record.get("category") == "set_holiday":
        return f"{emoji} SET Holiday · {record.get('title', 'Holiday')}"
    symbol = record.get("symbol") or record.get("title") or "Unknown"
    return f"{emoji} {kind} · {symbol}"


def event_description(record):
    items = "".join(
        "<li><b><u>"
        f"{html.escape(label)}"
        "</u></b>: "
        f"{html.escape(display(value))}"
        "</li>"
        for label, value in description_lines(record)
    )
    return f"<ul>{items}</ul>"


def idempotency_key(record):
    parts = [str(value) for value in semantic_identity(record)]
    if record.get("_idempotency_discriminator"):
        parts.append(record["_idempotency_discriminator"])
    identity = "|".join(parts)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def google_event_id(key):
    # Google event IDs accept base32hex characters; this prefix and SHA-256 hex do.
    return f"set{key}"


def canonical_event_content(body):
    return {
        "summary": body["summary"],
        "description": body["description"],
        "colorId": body["colorId"],
        "start": body["start"],
        "end": body["end"],
    }


def content_hash(body):
    serialized = json.dumps(
        canonical_event_content(body), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_event_body(record):
    start_date = date.fromisoformat(record["event_date_iso"])
    kind = event_kind(record)
    body = {
        "summary": event_summary(record),
        "description": event_description(record),
        "colorId": COLOR_IDS.get(kind, "8"),
        "start": {"date": start_date.isoformat()},
        "end": {"date": (start_date + timedelta(days=1)).isoformat()},
    }
    key = idempotency_key(record)
    body["extendedProperties"] = {
        "private": {
            "set_idempotency_key": key,
            "set_content_hash": content_hash(body),
            "set_source": "set_x_calendar_2026",
        }
    }
    return google_event_id(key), body


def load_existing_events(calendar, calendar_id):
    events = {}
    page_token = None
    while True:
        response = (
            calendar.events()
            .list(
                calendarId=calendar_id,
                timeMin=f"{YEAR}-01-01T00:00:00Z",
                timeMax=f"{YEAR + 1}-01-01T00:00:00Z",
                privateExtendedProperty=f"set_source={COLLECTION}",
                maxResults=2500,
                pageToken=page_token,
                showDeleted=False,
                singleEvents=True,
            )
            .execute(num_retries=3)
        )
        for event in response.get("items", []):
            events[event["id"]] = event
        page_token = response.get("nextPageToken")
        if not page_token:
            return events


def event_start_date(event):
    start = event.get("start") or {}
    if start.get("date"):
        return start["date"]
    return (start.get("dateTime") or "")[:10]


def event_in_period(event, month):
    iso_date = event_start_date(event)
    if len(iso_date) < 7 or not iso_date.startswith(f"{YEAR}-"):
        return False
    if month is None:
        return True
    return iso_date[5:7] == f"{month:02d}"


def delete_event(calendar, calendar_id, event_id, dry_run=False):
    if not dry_run:
        (
            calendar.events()
            .delete(calendarId=calendar_id, eventId=event_id)
            .execute(num_retries=3)
        )
    return "delete"


def sync_event(calendar, calendar_id, event_id, body, existing=None, dry_run=False):
    desired_hash = body["extendedProperties"]["private"]["set_content_hash"]

    if existing is None:
        if not dry_run:
            insert_body = {"id": event_id, **body}
            (
                calendar.events()
                .insert(calendarId=calendar_id, body=insert_body)
                .execute(num_retries=3)
            )
        return "create"

    existing_hash = (
        existing.get("extendedProperties", {}).get("private", {}).get("set_content_hash")
    )
    if existing_hash == desired_hash:
        return "skip"

    if not dry_run:
        (
            calendar.events()
            .update(calendarId=calendar_id, eventId=event_id, body=body)
            .execute(num_retries=3)
        )
    return "update"


def main():
    args = parse_args()
    db = get_firestore_client()
    records = load_2026_events(db, month=args.month)

    if not records:
        raise RuntimeError(
            f"No records found in {COLLECTION} for {YEAR}"
            + (f"-{args.month:02d}." if args.month else ".")
        )

    period = datetime(YEAR, args.month, 1).strftime("%B %Y") if args.month else str(YEAR)
    options = selection_options(records)
    options["dry_run"] = args.dry_run
    options["period"] = period
    configured_id, scopes = get_settings()
    options["configured_calendar_id"] = configured_id
    print(f"Loaded {len(records)} SET records for {period}.")
    try:
        selection = prompt_selection(options)
    except KeyboardInterrupt:
        print("\nPicker closed before a selection was submitted.")
        raise SystemExit(1) from None

    symbols = set(selection["symbols"])
    event_types = set(selection["event_types"])
    calendar_target = selection.get("calendar_target", "owned")
    selected = [
        item
        for item in records
        if record_matches(item[1], symbols, event_types)
    ]
    print(
        f"Selected {len(selected)} of {len(records)} records "
        f"({len(symbols)} tickers, {len(event_types)} event types)."
    )

    calendar = build("calendar", "v3", credentials=authorize(scopes))
    calendar_id, would_create = resolve_calendar_id(
        calendar, configured_id, calendar_target, dry_run=args.dry_run
    )
    if calendar_id:
        target_label = (
            OWNED_CALENDAR_NAME
            if calendar_target == "owned"
            else configured_id
        )
        print(f"Using calendar {target_label}: {calendar_id}")
        existing_events = load_existing_events(calendar, calendar_id)
    else:
        existing_events = {}
    counts = {"create": 0, "update": 0, "skip": 0, "delete": 0, "failed": 0}
    desired_ids = set()

    print(
        f"{'Dry run: ' if args.dry_run else ''}processing {len(selected)} "
        f"selected SET records for {period}."
    )
    for index, (firestore_id, record) in enumerate(selected, start=1):
        event_id, body = build_event_body(record)
        desired_ids.add(event_id)
        try:
            action = sync_event(
                calendar,
                calendar_id or OWNED_CALENDAR_NAME,
                event_id,
                body,
                existing=existing_events.get(event_id),
                dry_run=args.dry_run or would_create,
            )
            counts[action] += 1
            if args.verbose:
                print(
                    f"{action.upper():6} {body['start']['date']}  "
                    f"{body['summary']}  [{firestore_id}]"
                )
            elif index % 100 == 0 or index == len(selected):
                print(f"Processed {index}/{len(selected)} records...")
        except Exception as error:
            counts["failed"] += 1
            print(
                f"FAILED {body['start']['date']}  {body['summary']}: {error}",
                file=sys.stderr,
            )

    removals = [
        (event_id, event)
        for event_id, event in existing_events.items()
        if event_id not in desired_ids and event_in_period(event, args.month)
    ]
    for index, (event_id, event) in enumerate(removals, start=1):
        label = event.get("summary") or event_id
        when = event_start_date(event) or "-"
        try:
            action = delete_event(
                calendar,
                calendar_id,
                event_id,
                dry_run=args.dry_run or would_create,
            )
            counts[action] += 1
            if args.verbose:
                print(f"{action.upper():6} {when}  {label}  [{event_id}]")
            elif index % 100 == 0 or index == len(removals):
                print(f"Removed {index}/{len(removals)} unselected events...")
        except Exception as error:
            counts["failed"] += 1
            print(f"FAILED {when}  {label}: {error}", file=sys.stderr)

    print(
        "Summary: "
        f"create={counts['create']}, update={counts['update']}, "
        f"skip={counts['skip']}, delete={counts['delete']}, "
        f"failed={counts['failed']}"
    )
    if counts["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
