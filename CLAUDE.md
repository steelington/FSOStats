# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

FSOSTATS views Aces High **Friday Squad Operations** statistics, read
from the aceshigh.events log API into a local SQLite cache (the history
was originally scraped from ahevents.net's pages). It's a sibling to
E:\AHSTATS (the same idea for the main-arena tour stats) and will reuse
that project's UI components.

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

    fsostats/client.py      rate-limited client for the aceshigh.events API
    fsostats/parser.py      API JSON -> records; line patterns, derived stats
    fsostats/db.py          SQLite persistence (FSODB)
    fsostats/sides.py       infers Allied/Axis from aircraft flown
    fsostats/campaigns.py   frame numbers, curated titles, clustering
    fsostats/pilot.py       career, aircraft, frames, yearly trend
    fsostats/headtohead.py  who killed whom
    fsostats/squads.py      squad totals and name folding
    fsostats/leaderboard.py all-time boards and podium finishes
    fsostats/update.py      the in-app Update job
    fsostats/version_check.py  asks GitHub for a newer *app* release
    fsostats/settings.py    tiny JSON store for UI preferences
    fsostats/gui.py         the app; grid/chart/theme/tooltip beside it
    scrape_fso.py           resumable fetch; --workers N, --interval S
    tests/                  pytest; toy fixtures plus real-db invariants

## Light and dark (non-obvious)

**Every colour in `theme.py` is a `(light, dark)` pair**, and every
colour in `assets/fso_theme.json` is a two-element list. That is
customtkinter's own convention: a CTk widget handed a two-tuple picks
the right half for the current mode and re-picks it live when the mode
changes, so the CTk half of the app follows the masthead's Dark/Light
picker for free. A bare colour string is a bug - it shows the same
shade in both modes.

Nothing else follows automatically. `Canvas` (chart.py), the tooltip's
`Label`, and every ttk style take one flat string, so they resolve pairs
with `theme.color(...)` **at draw time** and re-draw on a switch:
`theme.set_mode()` re-runs `style_treeview()` and calls back everything
registered via `theme.on_mode_change()`, which `GridView` and
`TrendChart` do in their constructors.

The scrollbar styling covers `"TScrollbar"` as well as the
Vertical/Horizontal names. A `ttk.Scrollbar` keeps the *bare class name*
as its style whatever `-orient` says - only its layout is picked by
orientation - so styling only the two orientated names configures
nothing and leaves clam's light grey against a dark grid. AHSTATS hit
this first and pins it in `tests/test_widgets.py`.

The mode is remembered in `settings.py` and applied by
`ctk.set_appearance_mode(settings.get("appearance_mode"))` at import
time in gui.py, before any widget exists, so there is no dark flash on
the way to light.

## The update check

`version_check.py` asks the GitHub releases API of `steelington/FSOStats`
whether a newer **application** version exists - not to be confused with
`update.py`, which fetches newly-posted **frames** from ahevents.net.
One anonymous GET, no telemetry, nothing downloaded or installed; every
failure resolves to "no update" in silence. It runs on a daemon thread
at launch and, if something newer turns up, packs a badge into the
masthead that opens the release page in the browser.

`__version__` lives in `fsostats/__init__.py` (it used to be in gui.py,
which the checker cannot import without a cycle). Bump it in the same
commit that tags a release.

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

**A fresh clone has no database** - it has to fetch ~660 frames from the
API, about ten minutes at the rate limit the site sets. The app starts
anyway and says so rather than showing empty grids; see
`_show_empty_database_notice`. Point anyone who only wants to *use* it
at the exe.

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

**A frame with no pilots and no lines must come back `None`.**
Returning a frame-title-only event makes `get_fetched_event_ids()` treat
it as fetched, so it can never be retried.

**Classify from the sentence, not the API's action field.** New-site
frames store HiTech's raw vocabulary (`Start Flight`, `Kill Awarded`);
legacy ones store the scraper's (`sortie_start`, `kill`). The API sends
the page's sentence for both, and `parser._LOG_PATTERNS` turns it into
the same rows the 590k cached ones are. Verified on four legacy frames:
every participant and all 4,106 lines identical to the scraped copy.

## Fetching

The source is the read-only JSON API in the aceshigh.events log app
(E:\AHEVENTS\AHEvents_Joomla, `runApi()` in `logs/src/Application.php`):
`/logs/api/events/{TYPE}?since=` and `/logs/api/event/{ID}`, with TLS
verification on. ahevents.net is no longer contacted - its certificate
expired and it was found to carry a backdoor.

nginx limits `/logs/api/` to 2 requests/second per address, burst 20,
answering 429 beyond that; the client backs off and retries. 2 workers
at 1s by default, so raising the count buys nothing. A full rebuild is
about ten minutes.

The new site never posts side, squad or top-list totals for frames
uploaded there, so `parser._derive_stats` counts them from the log
using the old site's own rules (captures are not deaths, `crashed` is
always 0, city targets count as objects). Squad totals are always
derived; they matched the posted ones on 628 of 630 rows.

Frame 2168 (both teams labelled "Allies") is not served by the new
site, so a `--all` rebuild would lose it. Updates never touch it.

The fetch is resumable and doubles as the update job: it asks for the
events since `SCRAPE_SINCE` and fetches only what's new.

## Shipping

The app will ship with the database pre-filled plus an in-app Update
button. A onefile exe extracts to a temp dir wiped on every launch, so
the bundled database must be seeded once into a writable per-user
directory and only ever opened there - otherwise user updates vanish on
the next launch. See PLAN.md and `ahstats.paths.get_app_data_dir()`.
