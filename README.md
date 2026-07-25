# FSO Stats

Browse **Aces High Friday Squad Operations** statistics — 18 years of
them, from 2009 to today.

FSO logs live on [ahevents.net](http://ahevents.net) one frame at a
time. This scrapes them into a local SQLite database and puts a desktop
app on top, so a pilot's career, a squad's history, or a personal
record against one opponent is a couple of clicks rather than an
afternoon of reading log pages.

It's a companion to [AHStats](https://github.com/steelington/AHStats),
which does the same for main-arena tour statistics, and shares its
palette and grid components.

## What it shows

- **Frames and campaigns** — 583 frames grouped into 201 campaigns,
  switchable, with the site's inconsistent titles corrected.
- **Pilot** — career totals, aircraft flown with a real per-type K/D,
  every frame flown, and a kills-per-year trend.
- **Head-to-head** — who you shot down, who shot you down, and the full
  record against any named opponent. *The reason this exists:* an FSO
  log names both parties on every shootdown, so the whole 18 years of
  it is recoverable.
- **Squad** — totals, roster, frame-by-frame history and a trend.
- **Leaderboards** — all-time totals and podium finishes, for pilots
  and squads.

Every grid sorts, filters and exports to CSV. Hovering a column heading
explains what it counts, which matters more here than in most apps —
see the caveats below.

## Running it

Download `FSOStats.exe` from the releases page. It ships with every FSO
already scraped; the first launch unpacks the database into
`%LOCALAPPDATA%\FSOStats` and takes a few seconds. **Update** fetches
whatever has been posted since.

From source:

```
pip install -r requirements.txt
python app.py
```

## Building

```
python -m fsostats.prepare_db      # VACUUM, drop indexes, gzip
pyinstaller FSOStats.spec --clean
```

`prepare_db` drops the indexes before compressing — they're more than
half the file, and the app rebuilds them on first open in about two
seconds. That takes the shipped database from 37 MB to 18.6 MB.

The prepared `fsostats/data/fsostats.db.gz` is a build artefact and
isn't in the repository; it belongs with the exe as a release asset.
Without it, `scrape_fso.py` rebuilds the database from scratch — which
is a long, polite crawl against a small community site.

## Things the data does, that you'd want to know

The log is a community record kept by hand for eighteen years, and it
shows. The app surfaces these rather than hiding them:

- **A kill entry names the *victim's* aircraft**, not the killer's. Per
  aircraft statistics are recovered by matching each entry to the
  sortie it fell within.
- **A shootdown is written twice**, once per side. Counting both would
  double-count 46,586 of them.
- **73 events have no logs at all** — the site fails to render them,
  and has since around 2013.
- **47 frames hold only one team's log.** The other was never posted.
- **Side labels can't be trusted** — themed names, and a few frames
  labelled backwards — so the side each team actually fought for is
  inferred from the aircraft it flew, at 97.9% accuracy.
- **Titles and squad names drift** between frames. Both are normalised,
  and 53 frames are corrected by hand where no rule could.

`FINDINGS.md` documents all of it, with the evidence.

## Testing

```
pytest tests
```

112 tests: unit tests against a toy database with known answers, plus
integration checks asserting invariants on the real one.

## Licence and courtesy

The scraper waits three seconds between requests and runs three workers.
ahevents.net is a small community site run by volunteers — please don't
raise those numbers.
