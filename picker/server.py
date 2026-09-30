"""One-shot localhost page that returns a ticker and event-type selection."""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 1_000_000

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class PickerHandler(BaseHTTPRequestHandler):
    options = {}
    result = {}
    done = None

    def log_message(self, format, *args):
        return

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/options":
            self._send_json(200, self.options)
            return

        static = STATIC_FILES.get(path)
        if static is None:
            self._send_json(404, {"error": "Not found"})
            return

        filename, content_type = static
        body = (STATIC_DIR / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/selection":
            self._send_json(404, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "Selection is too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            selection = clean_selection(payload, self.options)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            self._send_json(
                400,
                {"error": "Selection must list symbols, event_types, and calendar_target"},
            )
            return

        self.result.clear()
        self.result.update(selection)
        self._send_json(200, {"ok": True})
        if self.done is not None:
            self.done.set()

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def clean_selection(payload, options):
    """Keep only symbols and event types that were offered on the page."""
    if not isinstance(payload, dict):
        raise ValueError("Selection must be an object")

    symbols = payload.get("symbols")
    event_types = payload.get("event_types")
    calendar_target = payload.get("calendar_target", "owned")
    if not isinstance(symbols, list) or not isinstance(event_types, list):
        raise ValueError("Selection must list symbols and event_types")
    if calendar_target not in {"owned", "configured"}:
        raise ValueError("calendar_target must be owned or configured")

    known_symbols = {item["symbol"] for item in options.get("symbols", [])}
    known_types = {item["type"] for item in options.get("event_types", [])}
    return {
        "symbols": _unique_known(symbols, known_symbols),
        "event_types": _unique_known(event_types, known_types),
        "calendar_target": calendar_target,
    }


def _unique_known(values, known):
    chosen = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or value not in known or value in seen:
            continue
        seen.add(value)
        chosen.append(value)
    return chosen


def prompt_selection(options):
    """Open the picker and block until the page submits one selection."""
    done = threading.Event()
    PickerHandler.options = options
    PickerHandler.result = {}
    PickerHandler.done = done

    server = ThreadingHTTPServer(("127.0.0.1", 0), PickerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Opening ticker picker at {url}")
    print("Submit a selection in the browser. Press Ctrl+C to cancel.")
    webbrowser.open(url)

    try:
        while not done.wait(0.5):
            pass
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    if not done.is_set():
        raise KeyboardInterrupt
    return dict(PickerHandler.result)
