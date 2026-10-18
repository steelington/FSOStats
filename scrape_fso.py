"""One-shot / resumable fetch of event logs from the aceshigh.events API
into fsostats.db. Safe to re-run: already-fetched events are skipped, so
this doubles as the "check for new events" update job.

Handles any of the site's eleven event types, one per run:

    python scrape_fso.py --type Snapshot

One type per invocation is deliberate. Each type resumes independently,
and a type whose listing page misbehaves cannot stall the others.

Usage:
    python scrape_fso.py [--type TYPE] [--workers N] [--interval SECONDS]

Only the fetch+parse runs in threads; every database write happens on
the main thread, since FSODB holds one sqlite connection and isn't safe
to share.

Each worker carries its own client and so its own rate limiter, meaning
--workers N allows up to N requests per --interval. The server allows 2
requests a second per address (burst 20) and answers anything faster
with a 429, which the client waits out - so raising N past the default
of 2 at 1s buys nothing. A full rebuild is ~660 frames, about ten
minutes.
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
from fsostats.parser import EVENT_TYPES, event_from_api, listings_from_api

DEFAULT_WORKERS = 2
DEFAULT_INTERVAL = 1.0

# Everything up to and including this date is already scraped, hand
# checked and titled (fsostats/campaigns.py), and ships in the database.
# Ignoring it keeps upsert_event_listings away from historical rows: it
# does `title=excluded.title` for every listing it is given, so any run
# covering old events would overwrite their curated titles.
#
# 24 Jul 2026 is August Storm Frame 3, the last frame of a complete
# campaign. Move this forward only when the newest campaign is complete.
#
# This cutoff is FSO-only. No other type has been scraped or curated, so
# for those the default is to take everything (see _parse_args).
SCRAPE_SINCE = "2026-07-24"


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", dest="event_type", default="FSO", choices=EVENT_TYPES,
                        metavar="TYPE",
                        help="event type to scrape (default FSO). One of: "
                             + ", ".join(EVENT_TYPES))
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"parallel fetchers (default {DEFAULT_WORKERS})")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"min seconds between requests per worker (default {DEFAULT_INTERVAL})")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                        help=f"ignore events on or before this date "
                             f"(FSO defaults to {SCRAPE_SINCE}; other types take everything)")
    parser.add_argument("--all", action="store_true",
                        help="ignore --since and consider every event on the site. "
                             "Rebuilds from scratch; overwrites curated titles, so "
                             "re-run with --retitle afterwards.")
    parser.add_argument("--report", default=None, metavar="PATH",
                        help="TSV of every event that did not come back clean "
                             "(default scrape_report_<type>.tsv)")
    parser.add_argument("--db", default=None, metavar="PATH",
                        help="database to write (default fsostats.db for FSO, "
                             "scrape_<type>.db otherwise)")
    args = parser.parse_args()

    # Only FSO has a curated history to protect; everything else starts empty.
    if args.since is None and args.event_type == "FSO":
        args.since = SCRAPE_SINCE

    if args.report is None:
        args.report = f"scrape_report_{args.event_type}.tsv"

    # fsostats.db is the FSOStats app's shipped database, and the GUI has
    # no event-type filter - it was written when FSO was all there was. So
    # other types go to their own file rather than making an FSO-only app
    # display Snapshots. Pass --db explicitly to override.
    if args.db is None and args.event_type != "FSO":
        args.db = f"scrape_{args.event_type}.db"

    return args


def main():
    args = _parse_args()
    workers = max(1, args.workers)

    # One client per worker: the rate limiter is per-client, so sharing
    # one would serialise every worker behind the same interval and undo
    # the parallelism.
    clients = queue.Queue()
    for _ in range(workers):
        clients.put(FSOClient(min_interval=args.interval))

    db = FSODB(args.db) if args.db else FSODB()
    print(f"Writing to {db.path}", flush=True)

    print(f"Fetching {args.event_type} event list...", flush=True)
    listing_client = FSOClient(min_interval=args.interval)
    since = None if args.all else args.since
    listings = listings_from_api(listing_client.fetch_event_list(args.event_type, since=since))
    print(f"Found {len(listings)} {args.event_type} events"
          f"{f' on or after {since}' if since else ''}.", flush=True)

    if not listings:
        db.close()
        return

    if not args.all and args.since:
        # event_date is "YYYY-MM-DD HH:MM:SS", so a string compare against
        # a bare date is a plain date comparison.
        # The server's `since` is inclusive; the cutoff day is already done.
        recent = [l for l in listings if (l.event_date or "")[:10] > args.since]
        print(f"{len(listings) - len(recent)} on {args.since} ignored "
              f"(already scraped and titled); {len(recent)} in scope.", flush=True)
        listings = recent

    db.upsert_event_listings(listings)

    fetched = db.get_fetched_event_ids()
    to_fetch = [l for l in listings if l.event_id not in fetched]
    print(f"{len(to_fetch)} events need fetching ({len(fetched)} already cached), "
          f"{workers} worker(s) at {args.interval}s.", flush=True)

    def fetch_one(listing):
        """Runs on a worker thread: fetch and parse only, no DB access."""
        client = clients.get()
        try:
            return listing, event_from_api(client.fetch_event(listing.event_id)), None
        except Exception as e:
            return listing, None, e
        finally:
            clients.put(client)

    ok = failed = done = partial = 0
    start = time.monotonic()
    print_lock = threading.Lock()

    # Every event that did not come back clean, for the report. A failure
    # here is a real finding, not just noise: a CM confirmed some older
    # events cannot be rendered by the live site at all even though their
    # rows exist in its database, so this list is precisely the set that
    # only a database dump can recover.
    problems: list[tuple[str, int, str, str, str]] = []

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
                    problems.append((args.event_type, listing.event_id, listing.event_date,
                                     listing.title, f"error: {type(error).__name__}: {error}"))
                    print(f"[{done}/{len(to_fetch)}] event {listing.event_id}: "
                          f"ERROR {type(error).__name__}: {error}", flush=True)
                    traceback.print_exception(type(error), error, error.__traceback__)
                    continue
                if event is None:
                    failed += 1
                    problems.append((args.event_type, listing.event_id, listing.event_date,
                                     listing.title, "no data: no pilots or log lines"))
                    print(f"[{done}/{len(to_fetch)}] event {listing.event_id} ({listing.event_date}): "
                          f"no pilots or log lines, skipping", flush=True)
                    continue

                # Completeness, not just success. 31 events in the existing
                # data returned HTTP 200 with plausible output and every log
                # entry on a single side - they render as if complete. Some
                # events genuinely are one-sided (the other team's log was
                # never posted), so this is recorded rather than rejected:
                # the side that did come back is real data.
                sides = {e.side for e in event.log_entries}

                if len(sides) < 2:
                    partial += 1
                    problems.append((
                        args.event_type, listing.event_id, listing.event_date, listing.title,
                        f"incomplete: {len(sides)} side(s) "
                        f"({', '.join(sorted(sides)) or 'none'}), "
                        f"{len(event.log_entries)} log lines",
                    ))

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
                flag = "  ** ONE SIDE ONLY **" if len(sides) < 2 else ""
                print(f"[{done}/{len(to_fetch)}] event {listing.event_id} ({listing.event_date}): "
                      f"{listing.title[:50]!r} - {len(event.log_entries)} log lines, "
                      f"{len(event.participants)} pilots. ETA {eta_min:.1f} min{flag}", flush=True)

    print(f"\nDone. {ok} fetched ({partial} of them one-sided), {failed} failed, "
          f"{len(fetched)} already cached.", flush=True)

    if problems:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("type\tevent_id\tevent_date\ttitle\tproblem\n")
            for row in sorted(problems, key=lambda r: r[1]):
                fh.write("\t".join(str(c).replace("\t", " ") for c in row) + "\n")
        print(f"{len(problems)} event(s) did not come back clean -> {args.report}", flush=True)

    # Campaign clustering is FSO-specific and unscoped: it reads every row
    # in the table and invents campaign names from date proximity, using
    # the curated FSO data in campaigns.py. Run on a Snapshot scrape it
    # would group unrelated weekly events into fictional campaigns and
    # overwrite their display titles.
    if args.event_type == "FSO":
        # New frames arrive untitled; this names them, and picks up any
        # correction added to campaigns.py since the last run.
        counts = db.apply_campaign_metadata()
        print(f"Named and grouped: {counts['curated']} curated, "
              f"{counts['clustered']} campaigns, {counts['excluded']} excluded, "
              f"{counts['one_sided']} frames with only one side's log.", flush=True)
    else:
        print(f"Skipping campaign clustering: it is curated for FSO only.", flush=True)

    # Side-role inference reads only the side names on each event, so it is
    # safe for any type.
    roles = db.apply_side_roles()
    print(f"Side roles inferred: {roles['allied']} allied, {roles['axis']} axis, "
          f"{roles['unknown']} unknown.", flush=True)
    db.close()


if __name__ == "__main__":
    main()
