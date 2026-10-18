"""Rate-limited HTTP client for the aceshigh.events log API.

aceshigh.events is the rebuilt Aces High events site, and its log
application serves the whole archive as JSON:

    /logs/api/events/{TYPE}[?since=YYYY-MM-DD]   every visible event of a type
    /logs/api/event/{ID}                         one frame, log lines and all

This replaced scraping ahevents.net, whose certificate expired in Dec
2025 and which turned out to carry a backdoor - every Update had every
user's app parsing pages from a compromised host with TLS checks off.
Verification is on here, as it should be.

The server limits /logs/api/ to 2 requests a second per address, with a
burst of 20. The client paces itself inside that, and if it ever gets a
429 anyway it backs off and tries again rather than failing the frame.
"""
from __future__ import annotations

import threading
import time

import requests

from fsostats import __version__

API_BASE = "https://aceshigh.events/logs/api"

# Says who we are rather than posing as a browser: the site owner can see
# this app's traffic in the logs, and tell versions apart if one misbehaves.
DEFAULT_HEADERS = {
    "User-Agent": f"FSOStats/{__version__} (+https://github.com/steelington/FSOStats)",
    "Accept": "application/json",
}

DEFAULT_MIN_INTERVAL = 1.0

# Attempts after a 429, waiting 2s, 4s, 6s. The bucket refills at 2/s, so
# the first wait alone is normally enough.
_RETRIES = 3


class FSOClient:
    """Thread-safe, rate-limited client for the aceshigh.events API."""

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL, timeout: float = 30.0,
                 base_url: str = API_BASE):
        self.min_interval = min_interval
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._lock = threading.Lock()
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_request_time)
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(_RETRIES + 1):
            self._throttle()
            resp = self._session.get(self.base_url + path, params=params, timeout=self.timeout)
            if resp.status_code == 429 and attempt < _RETRIES:
                time.sleep(2.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise AssertionError("unreachable")   # the last attempt returns or raises

    def fetch_event_list(self, event_type: str = "FSO", since: str | None = None) -> dict:
        """Every visible event of one type, newest first.

        `since` (YYYY-MM-DD) keeps only events on or after that day - the
        server compares inclusively, so a caller wanting "after" filters
        the boundary day itself.
        """
        params = {"since": since} if since else None
        return self._get(f"/events/{event_type}", params)

    def fetch_event(self, event_id: int) -> dict:
        """One frame: the event, participants, every log line, and the side
        totals and top lists where the site has them.

        Event ids are one space across every type, so no type is needed.
        """
        return self._get(f"/event/{int(event_id)}")
