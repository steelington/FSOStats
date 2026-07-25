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

**Still open: clustering the other 537 into campaigns.** Normalise the
title with `campaign_key()`, then walk frames in date order and attach
one to an open campaign when the key is similar enough *and* the gap is
under ~15 days. Two things it must handle: campaigns that recur years
apart (Battle of Britain 2012/2014/2016, Bougainville 2021/2025) need
the date gap as a hard cut, and titles whose word order reverses
mid-campaign need token-set rather than sequence similarity.

Validate against the numbered frames as free ground truth: group them
ignoring their numbers, then check the numbers that come back are
distinct and consecutive within each campaign. Duplicates mean a merge,
gaps mean a split. Note that "starts at frame 2" is *not* an error - the
73 missing logs mean a campaign can genuinely be missing its frame 1.

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

`grid.py` - sortable/filterable grids with totals footers.
`chart.py` - Canvas trend charts, no extra dependencies.
`theme.py` - palette and ttk styling.
Identity groups - pilots rename over 18 years; same problem AHSTATS
already solved, worth porting.

## Caveats to surface in the UI

- 73 events have no logs (ahevents.net server error, mostly 2008-2011),
  so career totals start effectively around 2009.
- Side labels are unreliable; use the inferred role, and note that a few
  frames are mislabelled at source.
- A pilot's totals only cover frames whose logs were published.

## Suggested build order

1. Campaign grouping + validation (everything else depends on the unit)
2. Pilot view: career totals + frame list
3. Head-to-head
4. Aircraft / outcomes / side role
5. Squad view, leaderboards, trend charts
