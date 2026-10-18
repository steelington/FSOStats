"""Fetch newly-posted frames, for the in-app Update button.

An update asks the aceshigh.events API for the FSO events since
`SCRAPE_SINCE`, diffs that against what's cached and fetches only
what's new. Typically that is one frame on a Friday, and nothing at all
the rest of the week.

**FSODB is not thread-safe** - one sqlite connection, no locking. So the
worker thread only fetches and parses, and hands finished events back
through a queue for the caller to save on its own thread. That is the
same split `scrape_fso.py` uses, and the reason it must not change.

Everything on or before `SCRAPE_SINCE` is ignored. That keeps
`upsert_event_listings` away from historical rows, whose curated titles
it would otherwise overwrite, and keeps the request to the handful of
frames an update can actually be missing.
"""
from __future__ import annotations

import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from fsostats.client import FSOClient
from fsostats.parser import event_from_api, listings_from_api

# Two workers a second apart stay inside the server's 2 requests/second.
DEFAULT_WORKERS = 2
DEFAULT_INTERVAL = 1.0


@dataclass
class Progress:
    """One message from the worker to whoever is showing progress."""
    kind: str                 # status | event | done | error
    text: str = ""
    fraction: float = 0.0
    event: object = None      # a parsed FSOEvent, for kind == "event"
    listing: object = None    # its EventListing, for kind == "event"
    added: int = 0
    failed: int = 0


def save(db, message: Progress) -> None:
    """Store one finished event. Call it on the database's own thread.

    The listing goes in first: `save_event` only UPDATEs the events row,
    so a frame with no row yet - every new one - would save its log
    under no event at all and never appear in the frame list.
    """
    db.upsert_event_listings([message.listing])
    db.save_event(message.event)


class Updater:
    """Runs the update on a background thread.

    Poll `messages` from the UI thread and pass any Progress carrying an
    `event` to `save()` there, since only that thread may touch the
    database.
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
            listings = listings_from_api(
                listing_client.fetch_event_list("FSO", since=self.since))

            # The server's `since` is inclusive; the cutoff day itself is
            # already in the database.
            recent = [l for l in listings if (l.event_date or "")[:10] > self.since]
            todo = [l for l in recent if l.event_id not in self.known]

            self.messages.put(Progress(
                "status", f"{len(recent)} frames since {self.since}, {len(todo)} new."))

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
                    return listing, event_from_api(client.fetch_event(listing.event_id)), None
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
                        # A frame with no pilots and no lines comes back
                        # None, so it stays retryable instead of being
                        # cached as an empty frame.
                        failed += 1
                        self.messages.put(Progress(
                            "status",
                            f"Event {listing.event_id}: no pilots or log lines yet.",
                            fraction))
                        continue
                    added += 1
                    self.messages.put(Progress(
                        "event",
                        f"[{done}/{len(todo)}] {listing.title[:44]} - "
                        f"{len(event.log_entries)} log lines",
                        fraction, event=event, listing=listing))

            self.messages.put(Progress(
                "done", f"Added {added} frame(s), {failed} failed.", 1.0,
                added=added, failed=failed))
        except Exception as error:                    # noqa: BLE001 - surfaced in the UI
            self.messages.put(Progress(
                "error", f"{type(error).__name__}: {error}\n"
                         f"{traceback.format_exc(limit=3)}"))
