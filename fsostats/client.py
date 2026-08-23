"""Rate-limited HTTP client for ahevents.net's event-logs page.

ahevents.net's TLS certificate expired (Dec 30 2025) and hasn't been
renewed - every visitor sees a browser warning right now. There's no
missing-intermediate trick to fix this like on hitechcreations.com (the
leaf cert itself is past its validity window), so we disable
verification for this host specifically. Acknowledged risk, accepted
for read-only public event-log data with the site owner's knowledge.
"""
from __future__ import annotations

import threading
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EVENT_LOGS_URL = "https://ahevents.net/index.php/event-logs"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://ahevents.net",
    "Referer": EVENT_LOGS_URL,
}

DEFAULT_MIN_INTERVAL = 3.0


class FSOClient:
    """Thread-safe, rate-limited client for ahevents.net."""

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL, timeout: float = 60.0):
        self.min_interval = min_interval
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._session.verify = False
        self._lock = threading.Lock()
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_request_time)
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.monotonic()

    def fetch_landing_page(self) -> str:
        self._throttle()
        resp = self._session.get(EVENT_LOGS_URL, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def fetch_event_log(self, event_id: int, event_type: str = "FSO") -> str:
        """Fetch one event's log page.

        The landing page carries eleven near-identical forms, one per event
        type, all posting to this same URL with the same `event_id` field
        and differing only in the submit button's value - `Get FSO Logs`,
        `Get Snapshot Logs`, and so on. So one endpoint serves every type
        and `event_id` is a single id space across all of them.
        """
        self._throttle()
        data = {"event_id": str(event_id), "action": f"Get {event_type} Logs"}
        resp = self._session.post(EVENT_LOGS_URL, data=data, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def fetch_fso_event_log(self, event_id: int) -> str:
        """Back-compatible alias; the GUI's updater calls this."""
        return self.fetch_event_log(event_id, "FSO")
