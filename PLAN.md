# FSOSTATS - GUI feature plan

Drafted 25 Jul 2026. What the app should show, and what the data can
actually support. See FINDINGS.md for the state of the data layer.

## Shape of the data

One `events` row = one **frame**. An FSO **event/campaign** is normally
three frames on consecutive Fridays. Users think in both units, so most
views need a frame/campaign toggle.

### Campaign grouping - frame numbers are solved, clustering isn't

**Frame numbers: done** (`fsostats/campaigns.py`). 537 of 587 frames
(91.5%) state their number once digits, word-numbers (`Frame One`) and
Roman numerals (`Frame I`) are all handled - the earlier 88% figure
counted digits only. The remaining 50 are corrected by hand in
`OVERRIDES`, so every frame with logs now has a number or is a declared
one-off. `apply_campaign_metadata()` writes the results to
`events.display_title` / `campaign` / `frame_no`.

Reading each unnumbered frame's neighbours in date order is what settled
them: 34 are a frame 1 that omitted the number while its own frames 2-3
are numbered. **Do not assume unnumbered means frame 1** - 3268 (Case
Blue), 3604 (Kursk) and 3938/3941 (Tunisia) sit in the *middle* of a
numbered run.

Also found: 3913 types the year as 1954 instead of 1945, so it won't
title-match its own frames; and 2168/2169 plus 2756/2757 are duplicate
submissions - same date, same title, identical log counts - which would
double-count every pilot in those frames. Both duplicates are in
`EXCLUDED`, with `Training` and a 2010 test event.

**Clustering: done** (`campaigns.cluster`). 583 frames group into **201
campaigns, 187 of them a complete three**, with no unexplained
frame-number errors.

A frame joins an open campaign when the titles are similar enough, the
gap is under 15 days, *and the frame number isn't already taken*. Using
the numbers as a constraint during clustering - not only to check it
afterwards - is what makes it reliable; it prevents two campaigns
merging and let the date window stay wide enough to span a missing
middle frame. Similarity blends token overlap (which survives the word
order reversing mid-campaign) with sequence matching (which handles
spelling drift).

Validation used the numbered frames as free ground truth and earned its
keep - every failure it reported was real:

* **Campaign names repeat across years**, so the name cannot be the
  identity. Grouping by it merged three separate Pantelleria campaigns
  into one nine-frame campaign numbered 1,1,1,2,2,2,3,3,3. Repeated
  names now carry their year.
* **Two campaigns renamed mid-run** were found as `[1, 3]` with the
  middle frame stranded elsewhere: "September FSO - B.O.B 1940 Frame 2"
  and "PRIVATEERS Solomons 1943 Frame 2". Both now curated.
* **3776 is titled "Frame 2"** a week after the real frame 2. Curated
  as frame 3.
* **3540/3541 are one frame, not two** - "Frame 3.1" and "Frame 3.2",
  one side each.

The single remaining flag is correct: Crimean Catastrophe (2016) is
`[1, 3]` because frame 2 is one of the 73 events the site cannot render.
"Starts at frame 2" is likewise not an error.

## Core views

### Pilot (the main one)

Entry box like AHSTATS, but back it with type-ahead over the 4,331 known
pilots - FSO handles are easy to mistype.

Career totals: FSOs participated in, frames flown, sorties, kills,
assists, ground kills, deaths, and the outcome split (landed / bailed /
ditched / captured / crashed).

**Head-to-head** - the feature worth building first. `log_entries` names
`other_pilot` on both kills and deaths, so:
  * who I killed, ranked
  * who killed me, ranked (nemesis)
  * full record against any named pilot across 18 years

Nothing in AHSTATS can do this; it's the reason this app is interesting.

Also per pilot:
  * aircraft flown (`sortie_start`), with K/D per type
  * side flown per frame (via `sides.py`) and record on each
  * kills/sorties trend across 18 years - straight into AHSTATS `chart.py`

### Frames / campaigns

List of frames with the pilot's result in each. Selecting one shows the
frame detail; toggling to campaign aggregates its three frames.

### Squad

`squad_stats` is already per event/side/squad: roster, totals, and
performance over time.

### Leaderboards

`top_entries` holds per-event top pilots and squads (`pilot_kills`,
`pilot_obj`, `squad_kills`, `squad_obj`). Aggregate for all-time boards.

## Ported from AHSTATS (E:\AHSTATS)

`grid.py` - sortable/filterable grids, since extended here with a
per-grid quick filter, row count and CSV export.
`chart.py` - Canvas trend charts, no extra dependencies. Its x-axis
label was hard-coded to "Tour"; the caller names it now.
`theme.py` - palette and ttk styling, unchanged so the two apps match.
`paths.py` - resource and app-data paths for a frozen build.
Identity groups - **not yet ported.** Pilots rename over 18 years; same
problem AHSTATS already solved.

## Caveats to surface in the UI

- 73 events have no logs (ahevents.net server error, mostly 2008-2011),
  so career totals start effectively around 2009.
- Side labels are unreliable; use the inferred role, and note that a few
  frames are mislabelled at source.
- A pilot's totals only cover frames whose logs were published.

## Build order - all done (25 Jul 2026)

1. ~~Campaign grouping + validation~~ - 201 campaigns, 187 complete
2. ~~Pilot view: career totals + frame list~~
3. ~~Head-to-head~~
4. ~~Aircraft / outcomes / side role~~ - roles in the `side_roles` table
5. ~~Squad view, leaderboards, trend charts~~

Also built since: frame detail, click-through between every view,
per-grid quick filter and CSV export, hover tooltips carrying the data
caveats, the in-app Update button, and 112 tests.

## What's left

**Packaging.** No `.spec` yet. `paths.get_database_path()` has the
seed-into-`%LOCALAPPDATA%` logic but has never run frozen, and that is
exactly where the onefile trap bites - see FINDINGS.md. `VACUUM` needs
to be a build step; it reclaims 12 MB.

**Identity groups.** Pilots rename across 18 years and their careers
split across handles. Case is folded already; renames are not. AHSTATS
solved this and it's worth porting.

**Smaller:** a participation-over-time chart would put the decline in
the app - median pilots per frame went 471 in 2009 to 66 in 2025, though
2026 is flat so far, and the schedule never missed a year.

**Open data questions:** 4 kill rows still name a victim who isn't a
participant of a two-sided frame; `sides.py` has an untested idea about
weighting affinity non-linearly.

## Next: v0.2.1 - fix Update, move to aceshigh.events (18 Oct 2026)

A user reported Update "doing nothing": the top frame stayed 24 Jul 2026.

**The Update bug - fixed in the working tree, not yet committed.**
`save_event()` only UPDATEs the `events` row; `upsert_event_listings()`
is what creates it, and `scrape_fso.py` calls it but the in-app Updater
never did. So every new frame saved its logs under no event row, never
reached the frame list, and the status bar still said "Added 9
frame(s)". Fix: `update.save()` upserts the listing first, and gui.py
saves through it. Pinned by `tests/test_update.py`, which fails without
the fix; 164 tests pass. Users' databases self-heal - the orphaned
frames never got `frame_title`, so the next Update re-fetches them and
`save_event` clears the orphan rows.

**Why switch sources now.** ahevents.net had a backdoor, and the client
talks to it with TLS verification off - every Update has every user's
app parse pages from a compromised host over an interceptable
connection. aceshigh.events (the rebuild, E:\AHEVENTS\AHEvents_Joomla)
is current, has valid TLS, and answers in ~1.5s against ~9s. Speed is a
side benefit only: the exe ships the database, so Update pulls a handful
of frames at most. The case for a JSON API is accuracy, not speed - see
step 1.

**What was checked on the live site:**
- FSO list `https://aceshigh.events/logs/events/FSO`, 100 per page,
  `?page=N` for older. 665 FSO events (old site lists 669 - unexplained).
- Event page `https://aceshigh.events/logs/event/{id}`, cached a day
  with an ETag. Markup: `section.log-side` > `h3.log-side-name`, then
  `div.squad-bar`, `div.pilot-bar`, `div.log-line` with `span.log-t`;
  also `stat-grid`, `side-summary`, `top-list`. Log text is the same
  wording, so `parser._LOG_PATTERNS` should carry over.
- Old frames keep their IDs and data: 4295 has the same 1,120 log lines
  and 66 pilots on both sites.
- Frames uploaded on the new site get **900xxx IDs** (900001-900011;
  900008/900010 not in the FSO list). Nothing after 4295 exists under an
  old-site ID, so if 0.2.1's bundled database takes the nine new frames
  from aceshigh.events there is nothing to de-duplicate. Never store
  4298-4319 from ahevents.net, or those frames exist twice.
- Neither the list nor the event page gives a time - only a date
  ("17 Oct 2026", "Saturday 17 October 2026"). There is no submitter.

**Bug on aceshigh.events: new frames are dated in UTC.**
`logs/src/Application.php:822` formats the HTC frame id's unix timestamp
with `date()`, which uses the droplet's zone (UTC). HTC and the legacy
data are US Central: `SNO 1784941194` is 19:59:54 to HTC and 00:59:54
UTC. So every Friday frame since the switch reads as Saturday. Fix:
format in `America/Chicago`, and correct the dates of the 900xxx rows
already stored (check each - a CM-typed or log-derived date may not need
shifting). Needs a deploy and a data fix on the droplet: one ssh session,
and ask first.

**Steps**
1. AHEvents: a read-only JSON API in the log app, plus the Chicago
   timezone fix, in one deploy. The HTML pages drop the time and the
   submitter; the API can return the full `event_date` and everything
   else straight from the tables, and FSOSTATS needs no HTML parser for
   the new site. Sketch:
   - `GET /logs/api/events/{TYPE}` - every visible event of a type:
     `source_id`, `title`, `event_date` (full, Central), `submitter`,
     `event_type`. Unpaged, or `?since=YYYY-MM-DD` - Update only wants
     what's newer than the bundled db.
   - `GET /logs/api/event/{id}` - one frame: participants (side, squad,
     pilot), log entries (side, pilot, seq, log_time, raw_text, plus the
     parsed action/data1/data2), side and squad stats, top lists.
   - Built on `EventRepository` - `buildSides()` and its neighbours
     already load all of this. Same visibility rules as the pages (no
     hidden or sandbox events), the same day-long cache and ETag as
     `showEvent()`, `Content-Type: application/json`. Matched in the
     router before `/events/` and `/event/`.
   - Decide whether FSOSTATS maps the API's parsed actions or keeps
     running `raw_text` through `parser._LOG_PATTERNS`. Raw text is the
     safer start - it guarantees new rows match the 590k already cached.
   Deploy, then correct the stored dates of the 900xxx rows. Both need a
   go-ahead; the droplet work is one ssh session.

   **Live since 18 Oct 2026** (AHEvents 5abcd84: `Application.php`,
   `EventRepository.php`, `deploy/aceshigh-events.conf`). Verified on
   the droplet: 4295 returns 66 participants and 1,120 lines, matching
   the old site. The nine FSO 900xxx dates are shifted UTC -> Central and
   all read Friday ~20:xx now (backup `~/datefix-20261018T063500Z.json`).
   Left alone: Scenario 900008/900010/900012 at 23:27 - probably UTC too
   (18:27 Central, same day), unconfirmed. The `limit_req` block is in
   the live nginx file too (backup `~/ahevents.nginx.bak-20261018`);
   a 30-request burst gave 24 x 200 then 429s, as designed. Exactly the sketch above. Envelope
   carries `api: 1` and `timezone: America/Chicago`; each log line has
   `side, side_slot, pilot, seq, time, action, data1, data2, text`, where
   `text` is the page's sentence (composed by `EventFormatter` for 900xxx
   rows, which store no prose). No API key, by decision: the data is
   public and a key inside the exe is not a secret. nginx instead gets a
   `limit_req` zone on `/logs/api/` (2r/s, burst 20, 429) - the live
   site file was rewritten by certbot, so that block is added to it by
   hand, not copied over. Smoke-tested under `php -S` with a stub
   database (routing, JSON, ETag/304, 400/404s, the HTML page after the
   `buildSides` refactor); the real SQL is only exercised once deployed.
   Send `User-Agent: FSOStats/<version>` from the client.
2. FSOSTATS: a client for the API returning the same
   `EventListing`/`FSOEvent`, TLS verification on; drop the expired-cert
   exception; update tests, status text ("Contacting ahevents.net..."),
   README and docstrings. Re-fetch the nine frames into the dev db from
   the API, then `apply_campaign_metadata()`/`apply_side_roles()`.

   **Done 18 Oct 2026, uncommitted.** `client.py` reads the API;
   `parser.py` lost its HTML walking (bs4/lxml dropped from requirements
   and excluded in the spec) and gained `listings_from_api`,
   `event_from_api` and `_derive_stats`. Two sentence patterns widened
   for the new site's wording - uncredited deaths ("Was shot down.",
   "Crashed.") and "Destroyed an ...". Legacy frames 4295/4219/4140/3500
   fetched through the API are identical to their scraped copies. Dev db
   now has the nine new frames: 592 frames, 204 campaigns, roles right.
   179 tests (`tests/test_api.py` new).
3. Commit, bump `__version__` to 0.2.1, `prepare_db`, build, release.
   The in-app badge then moves 0.2.0 users off the old site.

   **Built 18 Oct 2026, not released.** `dist/FSOStats.exe` 45.5 MB
   (bs4/lxml gone), shipped db 18.9 MB with 592 frames. Run locally:
   first run into an empty LOCALAPPDATA unpacks and opens on the nine
   new frames; a copy of a real 0.2.0 database updates through the
   button ("Added 9 frame(s)") to identical figures; Frame detail shows
   derived team totals and the submitter. Testing first run frozen found
   0.2.0's invisible-window bug (see CLAUDE.md) - fixed.

   **Released 18 Oct 2026:** tag v0.2.1 on Gitea and GitHub,
   https://github.com/steelington/FSOStats/releases/tag/v0.2.1 with
   FSOStats.exe (sha256 fd22c10a...; the db is bundled inside, as in
   0.2.0). The latest-release API answers v0.2.1, so 0.2.0 installs show
   the Update-available badge.
