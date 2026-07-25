# FSOSTATS - GUI feature plan

Drafted 25 Jul 2026. What the app should show, and what the data can
actually support. See FINDINGS.md for the state of the data layer.

## Shape of the data

One `events` row = one **frame**. An FSO **event/campaign** is normally
three frames on consecutive Fridays. Users think in both units, so most
views need a frame/campaign toggle.

### Campaign grouping is the one hard problem

Only 516 of 586 frames (88%) have a parseable frame number. The rest use
`Fr1` / `Fr 3`, Roman numerals (`Frame I`), or nothing at all - "North
Sea Raiders" appears twice with no number.

Proposed approach: normalise the title (strip frame markers, punctuation,
case) to get a campaign key, then cluster by date, since frames run about
a week apart. Date proximity resolves the unnumbered repeats that title
matching alone can't. Needs validating against the 88% that *are*
numbered - they're free ground truth.

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
