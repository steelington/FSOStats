# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

FSOSTATS views Aces High **Friday Squad Operations** statistics, scraped
from ahevents.net into a local SQLite cache. It's a sibling to E:\AHSTATS
(the same idea for the main-arena tour stats) and will reuse that
project's UI components.

**Read `PLAN.md` and `FINDINGS.md` first.** They hold the feature plan,
the data-layer bugs already fixed, and the open TODOs. Don't re-derive
them from the code.

## State (July 2026)

Data layer works; the GUI hasn't been started. 586 of 659 events have
logs, 589,621 log entries, 4,331 pilots, 2008-2026. Not yet a git repo.

## Layout

    fsostats/client.py   rate-limited HTTP client for ahevents.net
    fsostats/parser.py   BeautifulSoup parsing of the event-log pages
    fsostats/db.py       SQLite persistence (FSODB)
    fsostats/sides.py    infers Allied/Axis from aircraft flown
    scrape_fso.py        resumable scrape; --workers N, --interval S

## Things that will bite you

**One `events` row is one frame, not one event.** An FSO event is
normally three frames on consecutive Fridays. Users think in both units.
Grouping frames into campaigns is unsolved - only 88% of titles have a
parseable frame number. See PLAN.md.

**Only `sortie_start` says what a pilot flew.** A `kill` log entry names
the *victim's* aircraft. Counting planes across all actions makes both
sides look identical, which reads like a parser bug and isn't.

**Side names are per-event and unreliable.** As well as Allies/Axis there
are themed names (USAAF/Luftwaffe, Blue Thunder); some frames label both
teams identically, and a few have the labels swapped outright. Use
`sides.py`, which infers the role from aircraft, rather than trusting the
label. Never re-introduce an allowlist of side names - that bug cost 184
events.

**FSO substitutes aircraft across sides.** Finland flies P-40Cs and
Brewster B-239s; Italian C.205s appear on the Allied side after 1943. No
single aircraft type identifies a side, which is why `sides.py` weights a
vote across all of them.

**`FSODB` is not thread-safe** - one sqlite connection, no locking. The
scraper fetches in parallel but writes only from the main thread. Keep it
that way.

**A parse that finds no sides must return `None`.** Returning a
frame-title-only event makes `get_fetched_event_ids()` treat it as
fetched, so it can never be retried.

## Scraping

ahevents.net's TLS certificate expired in Dec 2025, so the client
disables verification for that host - a documented, accepted risk for
read-only public data.

3 seconds between requests per worker, 3 workers by default. Most of the
wall time is the site's own ~9s response, not our limit. It's a small
community site; don't raise the worker count much.

The scrape is resumable and doubles as the update job: it diffs the
landing page and fetches only what's new.

## Shipping

The app will ship with the database pre-filled plus an in-app Update
button. A onefile exe extracts to a temp dir wiped on every launch, so
the bundled database must be seeded once into a writable per-user
directory and only ever opened there - otherwise user updates vanish on
the next launch. See PLAN.md and `ahstats.paths.get_app_data_dir()`.
