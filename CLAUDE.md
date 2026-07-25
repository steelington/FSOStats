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

## State (25 July 2026)

Data layer and GUI both work. 660 events, 587 with logs, 590,741 log
entries, 4,318 pilots, 2009-2026. Of those, 583 frames are shown -
4 are excluded as duplicates or test events - grouped into 201
campaigns. Run with `python -m fsostats.gui`; tests with `pytest tests`
(112 of them).

Git remote is Gitea at http://192.168.188.140:3000/steely/FSOStats.
AHSTATS is there too, and keeps its GitHub remote as `github` for when
it ships.

**Not done:** packaging (no .spec; `paths.get_database_path()` has the
seed-into-%LOCALAPPDATA% logic but has never run frozen), and identity
groups for pilots who rename - AHSTATS solved that one and it's worth
porting.

## Layout

    fsostats/client.py      rate-limited HTTP client for ahevents.net
    fsostats/parser.py      BeautifulSoup parsing of the event-log pages
    fsostats/db.py          SQLite persistence (FSODB)
    fsostats/sides.py       infers Allied/Axis from aircraft flown
    fsostats/campaigns.py   frame numbers, curated titles, clustering
    fsostats/pilot.py       career, aircraft, frames, yearly trend
    fsostats/headtohead.py  who killed whom
    fsostats/squads.py      squad totals and name folding
    fsostats/leaderboard.py all-time boards and podium finishes
    fsostats/update.py      the in-app Update job
    fsostats/gui.py         the app; grid/chart/theme/tooltip beside it
    scrape_fso.py           resumable scrape; --workers N, --interval S
    tests/                  pytest; toy fixtures plus real-db invariants

## Building the exe

    python -m fsostats.prepare_db      # VACUUM, drop indexes, gzip
    pyinstaller FSOStats.spec --clean  # -> dist/FSOStats.exe (47 MB)

`prepare_db` drops every index before compressing: they are more than
half the file (167 MB with, 78 MB without), and `FSODB`'s SCHEMA
rebuilds them on first open in ~2.5s. That halves the shipped database,
37.5 MB -> 18.6 MB, and guarantees it carries exactly the indexes
SCHEMA declares rather than whatever a dev machine accumulated.

**The onefile trap, and why it's handled the way it is.** A onefile exe
extracts to a temp directory Windows wipes on every launch, so the
bundled database is decompressed *once* into `%LOCALAPPDATA%\FSOStats`
and only ever opened there - otherwise every Update would vanish on the
next start. `paths.get_database_path()` seeds only when the target is
missing, writing to a `.partial` name and renaming, so an interrupted
first run can't leave a half-written file that later launches accept.

**Never create a second `CTk()` root.** First-run unpacking shows a
splash, and it must be a `CTkToplevel` of the main window. A second root
puts the masthead image in the wrong interpreter and tkinter dies with
`image "pyimage1" doesn't exist` - which only happens on first run, so
it will not show up in ordinary testing.

`fsostats/data/fsostats.db.gz` is a build artefact, gitignored. It
travels as a release asset beside the exe.

## Recomputing derived data

Three tables/columns are derived and must be refreshed after any scrape:

    db.apply_campaign_metadata()   display_title, campaign, frame_no,
                                   excluded, one_sided
    db.apply_side_roles()          the side_roles table

Both are cheap and idempotent. `scrape_fso.py` and the Update button
already call them; anything else that writes events must too.

## Things that will bite you

**One `events` row is one frame, not one event.** An FSO event is
normally three frames on consecutive Fridays. Users think in both units.
`campaigns.cluster()` groups them - 583 frames into 201 campaigns - and
`events.campaign` holds the result.

**A campaign's name is not its identity.** Names recur across years:
Battle of Britain ran four times, Pantelleria three. Grouping by name
merged three Pantelleria campaigns into one nine-frame campaign
numbered 1,1,1,2,2,2,3,3,3. Repeated names carry their year.

**Only `sortie_start` says what a pilot flew.** A `kill` log entry names
the *victim's* aircraft. Counting planes across all actions makes both
sides look identical, which reads like a parser bug and isn't. To get
the pilot's own aircraft, attribute each entry to the most recent
`sortie_start` at or before its `seq` - see `pilot.py`.

**A shootdown is logged twice**, as the killer's `kill` row and the
victim's `death` row. Counting both double-counts 46,586 of them;
counting only kills loses the 4,410 deaths no kill row covers.
`headtohead.py` takes every kill plus only the uncovered deaths.

**Handles and squad names fold.** 165 handles differ from another only
by case, so every pilot comparison is `COLLATE NOCASE` - and needs a
NOCASE index, since a plain one can't serve it. Squad names also vary by
spacing, decoration and punctuation, so `squads.squad_key()` keeps only
letters and digits. Never match squads by substring: II./JG11 is inside
III./JG11 Black Hearts and they are different units.

**Side names are per-event and unreliable.** As well as Allies/Axis there
are themed names (USAAF/Luftwaffe, Blue Thunder); some frames label both
teams identically, and a few have the labels swapped outright. Use the
`side_roles` table, inferred from aircraft by `sides.py`, rather than
trusting the label. Never re-introduce an allowlist of side names - that
bug cost 184 events.

**47 frames hold only one team's log**, flagged as `events.one_sided`.
The other side was never posted and never turned up later. Pilots on the
missing side get no credit for a frame they flew, and it explains 3,858
of the 3,862 kill rows naming a victim who isn't a participant.

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
