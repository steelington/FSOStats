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

## Head-to-head: how a shootdown is logged (25 Jul 2026)

One shootdown can be written twice, once from each side's log:

    kill    pilot = killer,  other_pilot = victim,  plane = the VICTIM's aircraft
    death   pilot = victim,  other_pilot = killer,  plane = null

`assist` is different again and easy to misread: `other_pilot` is the
*killer being helped*, and the victim is in `detail` ("Helps BTDiver
shoot down 800nates").

Neither table alone is complete:

| | rows | names the other party |
|---|---|---|
| `kill` | 93,137 | 93,137 (100%) |
| `death` | 78,265 | 50,996 (65%) |

The unnamed deaths are captures (10,042), crashes (9,182) and ack -
nobody to credit. Of the deaths that *do* name a killer, 91.4% have a
matching kill row and **4,410 do not**: a team's log can record being
shot down by someone whose own side's log never recorded the kill.

So `headtohead.py` takes every kill row and adds only those death rows
with no matching kill. Using both wholesale would double-count the
46,586 overlapping shootdowns; using kills alone would lose 4,410
engagements.

It also filters `events.excluded = 0` - the duplicate submissions
(2168/2169, 2756/2757) are byte-identical, so counting both would
double every engagement in those frames.

### Handles fold case

**165 handles differ from another only by capitalisation** (`11DUKE11`
and `11Duke11`), so every comparison is `COLLATE NOCASE` or a pilot's
record splits across spellings.

That needs its own indexes: a plain index cannot serve a NOCASE
comparison, and without `idx_log_pilot_nocase` / `idx_log_other_nocase`
a single lookup takes minutes rather than 0.27s. Both are in `SCHEMA`
now, along with an `other_pilot` index - head-to-head reads by the other
party as often as by the pilot.

Pilots who genuinely *renamed* are a separate problem; AHSTATS solved it
with identity groups and PLAN.md has porting that as an open item.

TODO: 3,862 kill rows name a victim who isn't a participant of that
event. Worth understanding before career totals are trusted - it may be
case drift, or a side roster the page didn't list.

## Squads and leaderboards (25 Jul 2026)

`squad_stats` is posted per event/side/squad by the site and **agrees
with the logs exactly** - on event 4295 the three biggest squads posted
69/52/30 kills and the log holds 69/52/30. Squad totals are taken from
it rather than recounted.

### Squad names drift like frame titles

248 raw names fold to 186. The key keeps only letters and digits, since
case, spacing, decoration and punctuation all vary between frames and
none of them distinguish one squad from another:

    '325th  Checkertails VFG'  '325th Checkertails VFG'
    'CLAIM JUMPERS'            'CLAIMJUMPERS'
    '9 GIAP VVS RKKA'          '9GIAP VVS RKKA'
    '~~~THE UNFORGIVEN~~~'     '~~~The Unforgiven~~~'

All 42 merges were checked by hand. **Substring matching is not done
and shouldn't be** - 'II./JG11' is inside 'III./JG11 Black Hearts' and
those are different units. '364th C-Hawks' and '364th C-Hawks FG' stay
apart for the same reason, though they may well be one outfit.

**A parser artifact turned up here:** some names carry literal
backslash-escapes from the source page - `162ndFG\\"Purple*Hearts\\"`
alongside the clean `162ndFG"Purple*Hearts"`. Dropping punctuation
absorbs it and the display name prefers the unescaped spelling, but the
parser is storing the escape. Worth fixing there if the data is ever
rebuilt; `SCRAPE_SINCE` means it won't be.

### Squads appearing on both sides - what it really is

A squad should never be on both sides of a frame. 118 cases look like
it; almost none are:

* **ZZZ_NO_SQUAD** - not a squad at all, but the site's bucket for
  pilots without one. 217 frames, both sides at once, and its roster
  includes a blank handle. Excluded from the squad list.
* **The same team listed twice on the same side** under two spellings,
  which is the backslash-escaping artifact above rather than two teams.
  11 groups. The source is inconsistent about which copy carries the
  roster and which the totals - event 3067's escaped `412th FNVG
  \\"Volunteers\\"` row has 25 pilots and 15 kills with *no*
  participants, while the clean row has the 25 participants and 0 kills.
  Neither summing nor taking the maximum matches the roster in all 11,
  so totals are summed and the pilot count is taken from `participants`,
  which is authoritative.
* **A genuine residue of 30 pilot-frames** (out of 108,094) where one
  pilot is recorded on both teams, logs and all: SKfgALPO in event 4232
  flew a Bf 110 for the Luftwaffe and also carries eight RAF entries.
  Left as the source records them.

Separately, `squad_stats` holds one row per side, so row count is not
frame count - JG54 Grunherz has 519 team entries across 514 frames.
Frames are counted as distinct event ids.

### top_entries ranks per side, not per frame

(event, side, category, rank) is unique, so **every frame has two
rank-1 pilots**, one per team. Categories are pilot_kills, pilot_obj,
squad_kills, squad_obj; the lists run 7 deep for pilots and 3 for
squads.

That depth means `top_entries` **cannot give all-time totals** - summing
it totals the podium, not the career, and flatters a pilot who was often
second over one who was steadily fourth. Totals are counted from
`log_entries` instead; `top_entries` answers a different question, which
is how often someone led at all.

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
