"""One-shot / resumable scrape of every FSO event log from ahevents.net
into fsostats.db. Safe to re-run: already-fetched events are skipped,
so this doubles as the "check for new events" monthly update job.

Usage:
    python scrape_fso.py [--workers N] [--interval SECONDS]

Most of the wall time is spent waiting on ahevents.net (roughly nine
seconds a page, against a three second rate limit), so fetching in
parallel is what actually speeds this up. Only the fetch+parse runs in
threads; every database write happens on the main thread, since FSODB
holds one sqlite connection and isn't safe to share.

Each worker carries its own client and so its own rate limiter, meaning
--workers N allows up to N requests per --interval. Keep N small: this
is someone's community site, and the default of 3 is already gentle
next to typical page response times.
"""
from __future__ import annotations

import argparse
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from fsostats.client import FSOClient
from fsostats.db import FSODB
from fsostats.parser import parse_fso_event, parse_fso_event_list

DEFAULT_WORKERS = 3
DEFAULT_INTERVAL = 3.0


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"parallel fetchers (default {DEFAULT_WORKERS})")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"min seconds between requests per worker (default {DEFAULT_INTERVAL})")
    return parser.parse_args()


def main():
    args = _parse_args()
    workers = max(1, args.workers)

    # One client per worker: the rate limiter is per-client, so sharing
    # one would serialise every worker behind the same interval and undo
    # the parallelism.
    clients = queue.Queue()
    for _ in range(workers):
        clients.put(FSOClient(min_interval=args.interval))

    db = FSODB()

    print("Fetching event list...", flush=True)
    listing_client = FSOClient(min_interval=args.interval)
    listings = parse_fso_event_list(listing_client.fetch_landing_page())
    print(f"Found {len(listings)} FSO events.", flush=True)
    db.upsert_event_listings(listings)

    fetched = db.get_fetched_event_ids()
    to_fetch = [l for l in listings if l.event_id not in fetched]
    print(f"{len(to_fetch)} events need fetching ({len(fetched)} already cached), "
          f"{workers} worker(s) at {args.interval}s.", flush=True)

    def fetch_one(listing):
        """Runs on a worker thread: fetch and parse only, no DB access."""
        client = clients.get()
        try:
            html = client.fetch_fso_event_log(listing.event_id)
            return listing, parse_fso_event(html, listing.event_id), None
        except Exception as e:
            return listing, None, e
        finally:
            clients.put(client)

    ok = failed = done = 0
    start = time.monotonic()
    print_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # imap-style: results arrive as they complete, and the main
        # thread does every write.
        for listing, event, error in pool.map(fetch_one, to_fetch):
            done += 1
            elapsed = time.monotonic() - start
            eta_min = (len(to_fetch) - done) / (done / elapsed) / 60 if elapsed and done else 0

            with print_lock:
                if error is not None:
                    failed += 1
                    print(f"[{done}/{len(to_fetch)}] event {listing.event_id}: "
                          f"ERROR {type(error).__name__}: {error}", flush=True)
                    traceback.print_exception(type(error), error, error.__traceback__)
                    continue
                if event is None:
                    failed += 1
                    print(f"[{done}/{len(to_fetch)}] event {listing.event_id} ({listing.event_date}): "
                          f"no data / unexpected page, skipping", flush=True)
                    continue

                # A save that fails - a malformed page violating a key,
                # say - must not take the rest of the run with it. The
                # write is transactional, so the event rolls back and
                # stays retryable while everything else continues.
                try:
                    db.save_event(event)
                except Exception as e:
                    failed += 1
                    print(f"[{done}/{len(to_fetch)}] event {listing.event_id}: "
                          f"SAVE FAILED {type(e).__name__}: {e}", flush=True)
                    continue

                ok += 1
                print(f"[{done}/{len(to_fetch)}] event {listing.event_id} ({listing.event_date}): "
                      f"{listing.title[:50]!r} - {len(event.log_entries)} log lines, "
                      f"{len(event.participants)} pilots. ETA {eta_min:.1f} min", flush=True)

    print(f"\nDone. {ok} fetched, {failed} failed, {len(fetched)} already cached.", flush=True)
    db.close()


if __name__ == "__main__":
    main()
