"""Turns aceshigh.events API responses into FSOSTATS's records.

The API (see client.py) returns structured JSON, so there is no page to
walk. What is left to do here is the part that keeps new frames
consistent with the 590k log rows already cached:

* **Log lines are classified from their sentence**, through the same
  `_LOG_PATTERNS` that classified every cached row when it was scraped
  from ahevents.net's pages. The API sends the sentence for every line -
  for frames uploaded on the new site, which store only HiTech's raw
  fields, it is composed server-side by the same code that renders the
  page - so a kill reads the same whichever site it came from.
* **seq is renumbered 1..n per (side, pilot)**, as the page scraper did.
* **Missing totals are derived from the log.** Frames uploaded on the new
  site have no posted side, squad or top-list figures at all, and the
  squad boards read nothing else; see `_derive_stats`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

# The response shape this build reads. The server bumps its `api` number
# only when it renames or removes something, so a different one means
# fields this code relies on may be gone - refuse rather than misread.
API_VERSION = 1


# ---------------------------------------------------------------------------
# event list
# ---------------------------------------------------------------------------

@dataclass
class EventListing:
    event_id: int
    submitter: str
    event_date: str  # "YYYY-MM-DD HH:MM:SS", US Central
    title: str
    event_type: str = "FSO"


# Every type the site keeps logs for. This is the authoritative spelling:
# it is what the API's /events/{TYPE} takes, and it matches the legacy
# database's event_type enum.
EVENT_TYPES = [
    "FSO", "Snapshot", "KOTH", "SEC", "Scenario", "TDI",
    "HMS", "CC", "FE", "SnS", "SDL",
]


# ---------------------------------------------------------------------------
# one event's full log
# ---------------------------------------------------------------------------

@dataclass
class Participant:
    side: str
    squad: str
    pilot: str


@dataclass
class LogEntry:
    side: str
    pilot: str
    seq: int
    timestamp: str
    action: str
    plane: str | None = None
    other_pilot: str | None = None
    detail: str | None = None
    raw_text: str = ""


@dataclass
class GroupStats:
    side: str
    squad: str | None  # None for side-wide totals
    pilots: int = 0
    kills: int = 0
    assists: int = 0
    obj_destroyed: int = 0
    deaths: int = 0
    landed: int = 0
    bailed: int = 0
    captured: int = 0
    crashed: int = 0
    ditched: int = 0
    discos: int = 0


@dataclass
class TopEntry:
    side: str
    category: str  # 'pilot_kills' | 'pilot_obj' | 'squad_kills' | 'squad_obj'
    rank: int
    name: str
    squad: str | None
    value: int


@dataclass
class FSOEvent:
    event_id: int
    frame_title: str
    participants: list = field(default_factory=list)  # list[Participant]
    log_entries: list = field(default_factory=list)  # list[LogEntry]
    squad_stats: list = field(default_factory=list)  # list[GroupStats]
    side_stats: list = field(default_factory=list)  # list[GroupStats]
    top_entries: list = field(default_factory=list)  # list[TopEntry]


# Two wordings exist for a death nobody was credited with. ahevents.net
# printed the template with the gap left in - "Was shot down by   ." - and
# aceshigh.events says what it means: "Was shot down.", "Crashed.". Both
# classify the same way, a death with no other_pilot.
#
# Likewise "Destroyed a ammo bunker" (old) and "Destroyed an ammo bunker"
# (new): the new site fixed the article.
_LOG_PATTERNS = [
    ("sortie_start", re.compile(r"^Departed from Field #(?P<field>\d+) in a (?P<plane>.+)$")),
    ("landed", re.compile(r"^Arrived Safely at Field #(?P<field>\d+)$")),
    ("death", re.compile(r"^Was shot down(?:\s+by)?\s*(?P<other>.*?)\s*(?:\((?P<how>exploded|crashed)\))?\s*(?P<captured>and captured)?\.?$")),
    ("death", re.compile(r"^(?P<how>Crashed)\.?$")),
    ("kill", re.compile(r"^Shot down a (?P<plane>.+) flown by (?P<other>.+)\.$")),
    ("assist", re.compile(r"^Helps (?P<other>.+) shoot down (?P<victim>.+)\.$")),
    ("kill", re.compile(r"^Awarded kill as (?P<other>.+) crashes in a (?P<plane>.+)\.$")),
    ("ground_kill", re.compile(r"^Destroyed (?P<detail>an? .+) at base #(?P<field>\d+)\s*$")),
    ("bailed", re.compile(r"^Bravely Bailed from damaged plane\.?$")),
    ("ditched", re.compile(r"^Skillfully ditched\.?$")),
    ("refuel", re.compile(r"^Takes on fuel/ammo/ord at field #(?P<field>\d+)\.?$")),
]


def _classify_log_line(desc: str) -> dict:
    for action, pattern in _LOG_PATTERNS:
        m = pattern.match(desc)
        if not m:
            continue
        gd = m.groupdict()
        result = {"action": action}
        if action == "death":
            result["other_pilot"] = gd.get("other") or None
            how = (gd.get("how") or "").lower() or None
            bits = [b for b in (how, "captured" if gd.get("captured") else None) if b]
            result["detail"] = ", ".join(bits) or None
        elif action == "kill":
            result["plane"] = gd.get("plane")
            result["other_pilot"] = gd.get("other")
        elif action == "assist":
            result["other_pilot"] = gd.get("other")
            result["detail"] = gd.get("victim")
        elif action == "sortie_start":
            result["plane"] = gd.get("plane")
            result["detail"] = gd.get("field")
        elif action in ("landed", "refuel"):
            result["detail"] = gd.get("field")
        elif action == "ground_kill":
            result["detail"] = f"{gd.get('detail')} at base #{gd.get('field')}"
        return result
    return {"action": "other"}


# ---------------------------------------------------------------------------
# API responses -> records
# ---------------------------------------------------------------------------

def _check_version(data: dict) -> None:
    if data.get("api") != API_VERSION:
        raise ValueError(f"aceshigh.events API version {data.get('api')!r}; "
                         f"this build of FSO Stats reads version {API_VERSION}. "
                         f"A newer release should read it.")


def listings_from_api(data: dict) -> list[EventListing]:
    """The events in an /events/{TYPE} response, newest first."""
    _check_version(data)
    return [
        EventListing(
            event_id=int(e["source_id"]),
            submitter=e.get("submitter") or "",
            event_date=e.get("event_date") or "",
            title=e.get("title") or "",
            event_type=e.get("event_type") or "FSO",
        )
        for e in data["events"]
    ]


def event_from_api(data: dict) -> FSOEvent | None:
    """One frame from an /event/{ID} response.

    Returns None for a frame with neither pilots nor log lines - the same
    contract the page parser had, so an empty frame is never stored as
    fetched and stays retryable.
    """
    _check_version(data)
    head = data["event"]
    # The title as posted, not the site's cleaned display_title: it is what
    # the page scraper stored for every cached frame ("August Storm Frame
    # 3", not "August Storm - Frame 3"), and campaigns.py does the cleaning.
    event = FSOEvent(event_id=int(head["source_id"]),
                     frame_title=head.get("title") or head.get("display_title") or "")

    # Pilots are matched case-insensitively within a side, as the site
    # does, and every row then carries one spelling - FSODB joins
    # participants to log_entries on the exact name.
    name_of: dict[tuple[str, str], str] = {}

    def add_pilot(side: str, pilot: str, squad: str) -> str:
        key = (side, pilot.lower())
        if key not in name_of:
            name_of[key] = pilot
            event.participants.append(Participant(side=side, squad=squad, pilot=pilot))
        return name_of[key]

    squad_of = {(p["side"], p["pilot"].lower()): p.get("squad") or "" for p in data["participants"]}

    for p in data["participants"]:
        add_pilot(p["side"], p["pilot"], p.get("squad") or "")

    # The API lists lines grouped by side and pilot, each pilot's in seq
    # order, which is all the renumbering below needs.
    seq: dict[tuple[str, str], int] = defaultdict(int)
    for line in data["log"]:
        side = line["side"]
        pilot = add_pilot(side, line["pilot"], squad_of.get((side, line["pilot"].lower()), ""))
        key = (side, pilot.lower())
        seq[key] += 1
        text = line.get("text") or ""
        event.log_entries.append(LogEntry(
            side=side, pilot=pilot, seq=seq[key], timestamp=line.get("time") or "",
            raw_text=text, **_classify_log_line(text),
        ))

    if not event.participants and not event.log_entries:
        return None

    derived_squads, derived_sides, derived_tops = _derive_stats(event)

    # Squad figures are never posted by the new site, so they always come
    # from the log - which is what the posted ones agreed with anyway.
    event.squad_stats = derived_squads

    counts = ("pilots", "kills", "assists", "obj_destroyed", "deaths", "landed",
              "bailed", "captured", "crashed", "ditched", "discos")
    if data.get("side_stats"):
        event.side_stats = [
            GroupStats(side=s["side"], squad=None, **{c: int(s.get(c) or 0) for c in counts})
            for s in data["side_stats"]
        ]
    else:
        event.side_stats = derived_sides

    if data.get("top"):
        event.top_entries = [
            TopEntry(side=t["side"], category=t["category"], rank=int(t["rank"]), name=t["name"],
                     squad=(t.get("squad") or None) if t["category"].startswith("pilot") else None,
                     value=int(t["value"]))
            for t in data["top"]
        ]
    else:
        event.top_entries = derived_tops

    return event


#: How deep each top list goes on the site: seven pilots, three squads.
_TOP_DEPTH = {"pilot_kills": 7, "pilot_obj": 7, "squad_kills": 3, "squad_obj": 3}


def _derive_stats(event: FSOEvent) -> tuple[list, list, list]:
    """Squad totals, side totals and top lists, counted from the log.

    The rules are the ones the old site's posted figures follow, worked
    out by recounting the log of every two-sided frame from 2025-26 and
    comparing column by column (630 squad rows):

    * `deaths` is every death except a capture, which counts as
      `captured` instead - 628 of 630 rows agree;
    * `obj_destroyed` includes city targets, which classify as `other`;
    * `discos` is "Vanished without a trace";
    * `crashed` is always 0. The old site never filled it in, so neither
      do we - a derived figure the posted ones never had would make new
      frames look different from old ones.

    Top lists keep only non-zero values. Every derived value matched the
    posted lists; who comes first in a tie is the only difference, and
    here ties go alphabetically.
    """
    squad_of = {(p.side, p.pilot): p.squad for p in event.participants}
    groups: dict[tuple[str, str], GroupStats] = {}

    def group(side: str, squad: str) -> GroupStats:
        if (side, squad) not in groups:
            groups[(side, squad)] = GroupStats(side=side, squad=squad)
        return groups[(side, squad)]

    for p in event.participants:
        group(p.side, p.squad).pilots += 1

    pilot_kills: dict = defaultdict(int)
    pilot_obj: dict = defaultdict(int)

    for e in event.log_entries:
        squad = squad_of.get((e.side, e.pilot), "")
        g = group(e.side, squad)
        if e.action == "kill":
            g.kills += 1
            pilot_kills[(e.side, e.pilot, squad)] += 1
        elif e.action == "assist":
            g.assists += 1
        elif e.action == "ground_kill" or (e.action == "other" and e.raw_text.startswith("Destroyed ")):
            g.obj_destroyed += 1
            pilot_obj[(e.side, e.pilot, squad)] += 1
        elif e.action == "death":
            if "captured" in (e.detail or ""):
                g.captured += 1
            else:
                g.deaths += 1
        elif e.action == "landed":
            g.landed += 1
        elif e.action == "bailed":
            g.bailed += 1
        elif e.action == "ditched":
            g.ditched += 1
        elif e.raw_text.startswith("Vanished without a trace"):
            g.discos += 1

    counts = ("pilots", "kills", "assists", "obj_destroyed", "deaths", "landed",
              "bailed", "captured", "crashed", "ditched", "discos")
    sides: dict[str, GroupStats] = {}
    for g in groups.values():
        total = sides.setdefault(g.side, GroupStats(side=g.side, squad=None))
        for c in counts:
            setattr(total, c, getattr(total, c) + getattr(g, c))

    tops: list[TopEntry] = []
    for side in sides:
        boards = {
            "pilot_kills": [(p, sq, v) for (s, p, sq), v in pilot_kills.items() if s == side],
            "pilot_obj": [(p, sq, v) for (s, p, sq), v in pilot_obj.items() if s == side],
            "squad_kills": [(g.squad, None, g.kills) for g in groups.values()
                            if g.side == side and g.squad],
            "squad_obj": [(g.squad, None, g.obj_destroyed) for g in groups.values()
                          if g.side == side and g.squad],
        }
        for category, rows in boards.items():
            ranked = sorted((r for r in rows if r[2] > 0), key=lambda r: (-r[2], r[0].lower()))
            for rank, (name, squad, value) in enumerate(ranked[:_TOP_DEPTH[category]], start=1):
                tops.append(TopEntry(side, category, rank, name, squad, value))

    return list(groups.values()), list(sides.values()), tops
