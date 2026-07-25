"""Fetch newly-posted frames, for the in-app Update button.

The scrape is already resumable, so an update is just the existing
scrape run against the landing page: it diffs what the site lists
against what's cached and fetches only what's new. Typically that is
one event on a Friday, and nothing at all the rest of the week.

**FSODB is not thread-safe** - one sqlite connection, no locking. So the
worker thread only fetches and parses, and hands finished events back
through a queue for the caller to save on its own thread. That is the
same split `scrape_fso.py` uses, and the reason it must not change.

Everything on or before `SCRAPE_SINCE` is ignored. That skips the 73
events ahevents.net cannot render - all 2008-2013, all of which would
fail on every run - and keeps `upsert_event_listings` away from
historical rows, whose curated titles it would otherwise overwrite.
"""
from __future__ import annotations

import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from fsostats.client import FSOClient
from fsostats.parser import parse_fso_event, parse_fso_event_list

DEFAULT_WORKERS = 3
DEFAULT_INTERVAL = 3.0


@dataclass
class Progress:
    """One message from the worker to whoever is showing progress."""
    kind: str                 # status | event | done | error
    text: str = ""
    fraction: float = 0.0
    event: object = None      # a parsed FSOEvent, for kind == "event"
    added: int = 0
    failed: int = 0


class Updater:
    """Runs the update on a background thread.

    Poll `messages` from the UI thread; save any Progress carrying an
    `event`, since only that thread may touch the database.
    """

    def __init__(self, since: str, known_event_ids=None,
                 workers: int = DEFAULT_WORKERS, interval: float = DEFAULT_INTERVAL):
        self.since = since
        self.known = set(known_event_ids or ())
        self.workers = max(1, workers)
        self.interval = interval
        self.messages: queue.Queue[Progress] = queue.Queue()
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- worker ---------------------------------------------------------

    def _run(self) -> None:
        try:
            self.messages.put(Progress("status", "Fetching the event list..."))
            listing_client = FSOClient(min_interval=self.interval)
            listings = parse_fso_event_list(listing_client.fetch_landing_page())

            recent = [l for l in listings if (l.event_date or "")[:10] > self.since]
            todo = [l for l in recent if l.event_id not in self.known]

            self.messages.put(Progress(
                "status",
                f"{len(listings)} events listed, {len(recent)} after {self.since}, "
                f"{len(todo)} new."))

            if not todo:
                self.messages.put(Progress("done", "Already up to date."))
                return

            # One client per worker: the rate limiter is per-client, so
            # sharing one would serialise every worker behind the same
            # interval and undo the parallelism.
            clients: queue.Queue[FSOClient] = queue.Queue()
            for _ in range(self.workers):
                clients.put(FSOClient(min_interval=self.interval))

            def fetch(listing):
                if self.stop_event.is_set():
                    return listing, None, None
                client = clients.get()
                try:
                    html = client.fetch_fso_event_log(listing.event_id)
                    return listing, parse_fso_event(html, listing.event_id), None
                except Exception as error:            # noqa: BLE001 - reported below
                    return listing, None, error
                finally:
                    clients.put(client)

            added = failed = done = 0
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for listing, event, error in pool.map(fetch, todo):
                    done += 1
                    fraction = done / len(todo)
                    if self.stop_event.is_set():
                        self.messages.put(Progress("done", "Stopped.", fraction,
                                                   added=added, failed=failed))
                        return
                    if error is not None:
                        failed += 1
                        self.messages.put(Progress(
                            "status",
                            f"Event {listing.event_id}: {type(error).__name__}: {error}",
                            fraction))
                        continue
                    if event is None:
                        # A parse finding no sides returns None, so the
                        # event stays retryable instead of being cached
                        # as an empty frame.
                        failed += 1
                        self.messages.put(Progress(
                            "status",
                            f"Event {listing.event_id}: no data / unexpected page.",
                            fraction))
                        continue
                    added += 1
                    self.messages.put(Progress(
                        "event",
                        f"[{done}/{len(todo)}] {listing.title[:44]} - "
                        f"{len(event.log_entries)} log lines",
                        fraction, event=event))

            self.messages.put(Progress(
                "done", f"Added {added} frame(s), {failed} failed.", 1.0,
                added=added, failed=failed))
        except Exception as error:                    # noqa: BLE001 - surfaced in the UI
            self.messages.put(Progress(
                "error", f"{type(error).__name__}: {error}\n"
                         f"{traceback.format_exc(limit=3)}"))
