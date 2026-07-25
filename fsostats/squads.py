"""Squads, and the drift in how their names are typed.

`squad_stats` is posted per event/side/squad by the site itself, and it
agrees with the logs exactly - spot-checked on event 4295, where the
three biggest squads posted 69/52/30 kills and the log holds 69/52/30.
So squad totals come from there rather than being recounted.

Names, though, are typed fresh each frame and drift the way frame titles
do: '325th  Checkertails VFG' with two spaces, '~~~THE UNFORGIVEN~~~'
against '~~~The Unforgiven~~~', 'CLAIM JUMPERS' against 'CLAIMJUMPERS',
'9 GIAP VVS RKKA' against '9GIAP VVS RKKA'. Grouping on the raw string
splits a squad's history across spellings, so the key keeps only letters
and digits: 248 raw names fold to 186.

Some of the drift is ours, not theirs - `162ndFG\\"Purple*Hearts\\"`
carries literal backslash-escapes from the source page, alongside the
clean `162ndFG"Purple*Hearts"`. Dropping punctuation absorbs both.

Every one of the 42 merges was checked by hand. **Substring matching is
still not done**, and shouldn't be: 'II./JG11' is inside 'III./JG11
Black Hearts' and those are different units, as are '56th FG' and "56th
FG Zemke's Wolfpack". Squads whose names merely resemble each other stay
apart - '364th C-Hawks' and '364th C-Hawks FG' are kept separate for
that reason, even though they may well be the same outfit.

A squad should never be on both sides of a frame, and almost never is.
The 118 apparent cases are, in order of frequency: ZZZ_NO_SQUAD (not a
squad - excluded below); a squad listed twice on the *same* side under
two spellings, which is the escaping artifact above rather than two
teams; and a genuine residue of 30 pilot-frames where one pilot is
recorded on both teams, logs and all. SKfgALPO in event 4232 flew a
Bf 110 for the Luftwaffe and also carries eight RAF entries. Those are
left as the source records them.
"""
from __future__ import annotations

import collections
import re

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")

# Not a squad: the site's bucket for pilots flying without one. It
# appears in 217 frames, on both sides at once, and its "roster"
# includes a blank handle. Listing it alongside real squads would put a
# fictional outfit at the top of every board.
PLACEHOLDER_KEYS = {"zzznosquad"}


def squad_key(name: str) -> str:
    """A squad name reduced to something stable across frames.

    Case, spacing, decoration and punctuation all vary between frames
    and none of them distinguish one squad from another, so none survive.
    """
    if not name:
        return ""
    return _NON_ALPHANUMERIC.sub("", name.casefold())


def _display_name(spellings: collections.Counter) -> str:
    """The spelling a squad used most often, ties broken by the longest -
    which keeps the fuller name rather than an abbreviation.

    Runs of whitespace are collapsed, so the winner doesn't carry the
    double space in '325th  Checkertails VFG'. Case and decoration are
    left alone: they are how the squad writes its own name.
    """
    best = max(
        spellings.items(),
        # A spelling carrying backslash-escapes from the source page is
        # never the one to show, however often it appears.
        key=lambda kv: ("\\" not in kv[0], kv[1], len(kv[0])),
    )
    return re.sub(r"\s+", " ", best[0]).strip()


def all_squads(db) -> list[dict]:
    """Every squad, with its career totals.

    Aggregated in Python: only ~240 raw names exist, so folding them
    after the SQL group-by is cheaper than teaching SQLite the fold, and
    keeps the normalisation rule in one place.
    """
    # One row per squad_stats entry, not pre-aggregated. A squad can
    # field teams on *both* sides of the same frame, so it gets two rows
    # for that event - summing per-spelling frame counts would then count
    # the frame twice. Event ids are collected into a set instead.
    # 13k rows is nothing to fold in Python.
    rows = db.execute("""
        SELECT s.squad, s.event_id, s.pilots, s.kills, s.deaths, s.obj_destroyed,
               substr(e.event_date, 1, 10) AS date
        FROM squad_stats s
        JOIN events e ON e.event_id = s.event_id AND e.excluded = 0
        WHERE s.squad IS NOT NULL AND s.squad <> ''
    """).fetchall()

    merged: dict[str, dict] = {}
    spellings: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for squad, event_id, slots, kills, deaths, obj, date in rows:
        key = squad_key(squad)
        if key in PLACEHOLDER_KEYS:
            continue
        spellings[key][squad] += 1
        entry = merged.setdefault(key, {
            "key": key, "events": set(), "pilot_slots": 0, "kills": 0,
            "deaths": 0, "obj": 0, "first_seen": date, "last_seen": date,
        })
        entry["events"].add(event_id)
        entry["pilot_slots"] += slots or 0
        entry["kills"] += kills or 0
        entry["deaths"] += deaths or 0
        entry["obj"] += obj or 0
        if date < entry["first_seen"]:
            entry["first_seen"] = date
        if date > entry["last_seen"]:
            entry["last_seen"] = date

    out = []
    for key, entry in merged.items():
        entry["frames"] = len(entry.pop("events"))
        entry["name"] = _display_name(spellings[key])
        entry["spellings"] = len(spellings[key])
        entry["kd"] = entry["kills"] / entry["deaths"] if entry["deaths"] else float(entry["kills"])
        out.append(entry)
    out.sort(key=lambda s: (-s["frames"], -s["kills"], s["name"].casefold()))
    return out


def spellings_for(db, key: str) -> list[str]:
    """Every raw spelling that folds to this squad key.

    Needed to query the other tables, which store the raw name.
    """
    rows = db.execute(
        "SELECT DISTINCT squad FROM participants WHERE squad <> '' "
        "UNION SELECT DISTINCT squad FROM squad_stats WHERE squad <> ''")
    return [r[0] for r in rows if squad_key(r[0]) == key]


def roster(db, db_spellings: list[str]) -> list[dict]:
    """Pilots who flew for a squad, busiest first."""
    if not db_spellings:
        return []
    marks = ",".join("?" * len(db_spellings))
    rows = db.execute(f"""
        SELECT p.pilot,
               COUNT(DISTINCT p.event_id) AS frames,
               MIN(substr(e.event_date,1,10)),
               MAX(substr(e.event_date,1,10))
        FROM participants p
        JOIN events e ON e.event_id = p.event_id AND e.excluded = 0
        WHERE p.squad IN ({marks})
        GROUP BY p.pilot COLLATE NOCASE
        ORDER BY frames DESC, p.pilot COLLATE NOCASE
    """, db_spellings)
    return [{"pilot": r[0], "frames": r[1], "first": r[2], "last": r[3]} for r in rows]


def history(db, db_spellings: list[str]) -> list[dict]:
    """The squad's result in each frame it flew, newest first."""
    if not db_spellings:
        return []
    marks = ",".join("?" * len(db_spellings))
    rows = db.execute(f"""
        SELECT substr(e.event_date,1,10) AS date,
               COALESCE(e.display_title, e.title) AS frame,
               s.side, s.pilots, s.kills, s.deaths, s.obj_destroyed, s.squad
        FROM squad_stats s
        JOIN events e ON e.event_id = s.event_id AND e.excluded = 0
        WHERE s.squad IN ({marks})
        ORDER BY e.event_date DESC
    """, db_spellings)
    return [
        {"date": r[0], "frame": r[1], "side": r[2] or "", "pilots": r[3] or 0,
         "kills": r[4] or 0, "deaths": r[5] or 0, "obj": r[6] or 0, "as_named": r[7]}
        for r in rows
    ]
