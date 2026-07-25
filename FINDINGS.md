# FSOSTATS - data layer findings (25 Jul 2026)

Working notes from fixing the scrape. Everything below is implemented
unless marked TODO.

## Bugs found and fixed

### 1. Only Allies/Axis frames were parsed (184 events lost)

`parser.py` had `if side not in ("Allies", "Axis"): continue`. Side names
are per-event and themed - USAAF/Luftwaffe, Blue Thunder - so every
frame using anything else parsed to nothing.

Fixed by identifying a side header structurally instead: it's an `<h2>`
inside a table, whereas page furniture ("Event Logs", "CB Login") is not.

### 2. Failed parses were recorded as successful fetches

`parse_fso_event` returned an event carrying only `frame_title`;
`save_event` stored it; `get_fetched_event_ids()` treats "has
frame_title" as fetched. So those 184 were excluded from every future
run - re-running the scraper would never have retried them.

A parse finding no sides now returns `None`, so it counts as a failure,
stays retryable, and appears in the scrape log.

### 3. Some frames label both teams the same

Event 2168 titles both sides "Allies" (a submitter error) with different
squads and totals - two real teams. The parser now suffixes the repeat
(`Allies (2)`) rather than merging opposing teams or dropping one. Same
treatment for a squad repeated within one side.

**Update (25 Jul 2026):** 2168 turned out to be a duplicate. `plissken`
submitted the same frame twice at the same timestamp, as 2168 and 2169;
all 1223 log entries match byte for byte, but 2169 carries proper
Allies/Axis labels. 2168 is now in `campaigns.EXCLUDED` and 2169 is the
copy kept. The parser behaviour still matters - it is what made the
mislabelled copy parse at all, which is how the pair was comparable in
the first place.

### 4. One bad event killed the whole run

`db.save_event` was called outside any try/except, so a single
`IntegrityError` aborted the scrape with ~90 events left. Saves are
transactional, so the event rolls back cleanly; the loop now catches,
reports, and continues.

### 5. Scraping was serial and slow

~12s per event, of which only 3s was our rate limit - the rest is
ahevents.net's response time. `scrape_fso.py` now takes `--workers N`
(default 3) and `--interval`. Fetch and parse run in a thread pool;
every DB write stays on the main thread, since `FSODB` holds one sqlite
connection and isn't thread-safe. One client per worker, because the
rate limiter is per-client.

3 workers takes the full run from ~34 min to ~10. Don't push it much
further - it's a small community site.

## Side classification (fsostats/sides.py)

Side labels can't be trusted: themed names, duplicate labels, and a few
frames with the labels swapped outright (event 3682 lists the team
flying Fw 190s and Bf 109s as "Allies").

Aircraft are reliable, so the role is inferred from them. Each type gets
an affinity from how one-sidedly it's used across labelled events; a
team's aircraft then vote, weighted by affinity and sorties.

**Only `sortie_start` says what a pilot flew.** A `kill` entry names the
victim's aircraft, so counting all actions mixes both teams together -
this looked like a parser bug at first and isn't.

Substitution is why no single type can decide it: Finland flies P-40Cs
and Brewster B-239s, Italian C.205s appear on the Allied side post-1943.
Of 113 types with >=30 sorties, 97 are decisive (>=90% one side) and 10
are genuinely mixed. Vehicles and Gunner-Observer carry no signal and
are excluded (Gunner-Observer alone is ~9,000 near-evenly-split sorties).

**Accuracy: 97.9%** by leave-one-out over 892 labelled teams, with
affinity never learned from the team being classified. The residue is
substitution-heavy Continuation War frames plus the genuinely reversed
ones.

## Open items

- **TODO: weight affinity non-linearly.** Event 3849's "Axis" team flew
  97 P-40Cs (mixed) and 16 He 111Hs (decisively Axis) and was called
  Allied on volume. Raising the vote to a power of the affinity would
  let decisive types outweigh substituted ones. Sweep the exponent and
  re-measure before adopting - untested.
- **TODO: persist the role.** Add a `side_role` column
  (allied/axis/unknown) alongside the parsed label rather than rewriting
  the label, so the raw page data stays faithful.
- **73 events return a PHP error page** from ahevents.net itself, not a
  scraper gap - **confirmed by the user reproducing it in a browser**
  (event 1915, 25 Jul 2026). Examples: 1915/1925/1929 (all three
  Mannerheim Cross frames), 3066.

  The site serves a pre-rendered cache file per event, and for these it's
  missing:

      891  fopen(.../jumi_src/jumi_src/logs_cache/cached_log_1915.html) -> false
      892  fwrite(false, ...)  expects resource, boolean given
      893  fclose(false)       expects resource, boolean given

  It's failing to *regenerate* the cache, not merely to read it - note
  the doubled `jumi_src/jumi_src/` in the path. The directory doesn't
  exist, so the write fails and the next two calls fall over on the
  boolean. So these logs won't return on their own; any event whose
  cache file was lost can't be re-rendered while that path is wrong.
  A one-line fix for whoever maintains ahevents.net, and it would
  recover 73 events of FSO history.

  They cluster in 2008-2013, consistent with old cache files being
  cleaned up. **Done (25 Jul 2026):** `scrape_fso.py` now ignores
  everything on or before `SCRAPE_SINCE` (2026-07-24), so the update run
  no longer re-requests all 73. They are also hidden from the frame list
  by `FSODB.get_frames()`.
- **Frames with genuinely swapped labels** (3682, 3734 confirmed) - the
  classifier finds them; decide whether to correct or just flag.
- `scrape.pid` was a stale lock from an interrupted run; nothing reads
  it. Delete or implement properly.

## Database size (for shipping with the exe)

Measured 25 Jul 2026, 586 events / 589,621 log entries:

| | Size |
|---|---|
| as-is | 109.1 MB |
| after `VACUUM` | 97.4 MB |
| gzip -9 | 22.8 MB |

**Decision: ship it embedded, as-is.** PyInstaller's onefile archive
compresses embedded data, so the database adds roughly 20-25 MB to the
exe rather than 97. Against AHStats' 41 MB baseline that's a ~60-70 MB
FSO exe, which is acceptable.

`VACUUM` before building - it reclaims 12 MB, and `save_event`'s
delete-then-insert cycle refragments the file on every rescrape, so this
needs to be a build step rather than a one-off.

Considered and rejected for now: dropping `log_entries.raw_text` would
save 19.3 MB uncompressed, but it's the original line each entry was
parsed from, and it's worth keeping while the side-classification work
may want to re-derive fields from it. Revisit if the exe gets too large.

The real cost of embedding is that onefile re-extracts ~97 MB to a temp
dir on every launch, which is a noticeable startup delay. If that
becomes annoying, a onedir build (or shipping the db alongside the exe)
avoids it and lets data update without re-downloading the app.

## Shipping model: pre-filled database + in-app Update

The exe ships with every FSO ever scraped, and an Update button pulls in
new frames as they're posted (typically one event each Friday).

**The trap: a onefile exe extracts to a temp dir that is wiped on every
launch.** A database opened at the bundled path is read-only in effect -
anything the user updates disappears when they close the app. So:

1. Bundle the database as a read-only asset.
2. On first run, copy it to a writable per-user location
   (`%LOCALAPPDATA%\FSOStats`), same idea as
   `ahstats.paths.get_app_data_dir()` in E:\AHSTATS.
3. Only ever open the writable copy.

Seed only when the target is missing, so an update is never clobbered by
a later launch. Worth versioning the seed too, so shipping a newer
pre-filled database can top up an existing install rather than either
overwriting the user's updates or being ignored forever.

The update itself needs no new fetch logic: `get_fetched_event_ids()`
already makes the scrape resumable, so Update is the existing scrape
against the landing page - it diffs and fetches only what's new. Run it
on a background thread with progress, as AHSTATS does for its sync.

Note the local copy refragments as the user updates (delete-then-insert
in `save_event`), so it grows past the shipped 97 MB over time. An
occasional `VACUUM` after updating would keep that in check.

## Not yet started

GUI. Plan is to copy `grid.py`, `chart.py` and `theme.py` from AHSTATS
(E:\AHSTATS) and adapt - they give sortable/filterable grids with
totals footers and Canvas-drawn trend charts with no extra dependencies.
