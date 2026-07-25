"""Parses ahevents.net's FSO event-log pages. Table-based HTML with no
classes/ids to hook into beyond a couple of bgcolor markers, so parsing
walks each side's table row by row as a small state machine: squad
header -> pilot header -> log lines -> squad stats -> (repeat) -> side
stats + leaderboards.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup


def _text(el) -> str:
    return el.get_text(strip=True).replace("\xa0", " ").strip() if el else ""


def _to_int(s: str, default=0):
    m = re.search(r"-?\d+", s.replace(",", ""))
    return int(m.group()) if m else default


# ---------------------------------------------------------------------------
# event list (landing page dropdown)
# ---------------------------------------------------------------------------

@dataclass
class EventListing:
    event_id: int
    submitter: str
    event_date: str  # "YYYY-MM-DD HH:MM:SS"
    title: str


_OPTION_RE = re.compile(
    r"^(?P<submitter>.+?):\s*(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}):\s*FSO:\s*(?P<title>.*)$"
)


def parse_fso_event_list(html: str) -> list[EventListing]:
    """Extract every FSO event from the <form name="FSO"> dropdown on the
    event-logs landing page."""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", attrs={"name": "FSO"})
    if form is None:
        return []
    select = form.find("select", attrs={"name": "event_id"})
    if select is None:
        return []

    listings = []
    for opt in select.find_all("option"):
        value = opt.get("value", "")
        if not value.isdigit():
            continue
        raw = _text(opt)
        m = _OPTION_RE.match(raw)
        if m:
            listings.append(
                EventListing(
                    event_id=int(value),
                    submitter=m.group("submitter"),
                    event_date=m.group("date"),
                    title=m.group("title"),
                )
            )
        else:
            listings.append(EventListing(event_id=int(value), submitter="", event_date="", title=raw))
    return listings


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


_LOG_PATTERNS = [
    ("sortie_start", re.compile(r"^Departed from Field #(?P<field>\d+) in a (?P<plane>.+)$")),
    ("landed", re.compile(r"^Arrived Safely at Field #(?P<field>\d+)$")),
    ("death", re.compile(r"^Was shot down by\s*(?P<other>.*?)\s*(?:\((?P<how>exploded|crashed)\))?\s*(?P<captured>and captured)?\.?$")),
    ("kill", re.compile(r"^Shot down a (?P<plane>.+) flown by (?P<other>.+)\.$")),
    ("assist", re.compile(r"^Helps (?P<other>.+) shoot down (?P<victim>.+)\.$")),
    ("kill", re.compile(r"^Awarded kill as (?P<other>.+) crashes in a (?P<plane>.+)\.$")),
    ("ground_kill", re.compile(r"^Destroyed (?P<detail>a .+) at base #(?P<field>\d+)\s*$")),
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
            bits = [b for b in (gd.get("how"), "captured" if gd.get("captured") else None) if b]
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


_LOG_LINE_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(.*)$")
_TOP_ENTRY_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<squad>.+)\):\s*(?P<value>\d+)$")
_TOP_SQUAD_RE = re.compile(r"^(?P<name>.+?):\s*(?P<value>\d+)$")


def _parse_group_stats_table(table, side: str, squad: str | None) -> GroupStats:
    stats = GroupStats(side=side, squad=squad)
    label_map = {
        "pilots": "pilots", "kills": "kills", "assists": "assists",
        "obj destroyed": "obj_destroyed", "objects destroyed": "obj_destroyed",
        "deaths": "deaths", "landed": "landed", "bailed": "bailed",
        "captured": "captured", "crashed": "crashed", "ditched": "ditched",
        "disco'd": "discos",
    }
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 4:
            continue  # skip leaderboard rows (colspan=3 single-cell rows handled elsewhere)
        for cell in cells:
            txt = _text(cell)
            if ":" not in txt:
                continue
            label, _, val = txt.partition(":")
            field_name = label_map.get(label.strip().lower())
            if field_name:
                setattr(stats, field_name, _to_int(val))
    return stats


def _parse_top_entries(table, side: str) -> list:
    entries = []
    category = None
    rank = 0
    category_map = {
        "top pilots: kills": "pilot_kills",
        "top pilots: objects destroyed": "pilot_obj",
        "top squads: kills": "squad_kills",
        "top squads: objects destroyed": "squad_obj",
    }
    for row in table.find_all("tr"):
        cell = row.find("td", attrs={"colspan": "3"})
        if cell is None:
            continue
        txt = _text(cell)
        key = txt.lower()
        if key in category_map:
            category = category_map[key]
            rank = 0
            continue
        if category is None:
            continue
        rank += 1
        if category.startswith("pilot"):
            m = _TOP_ENTRY_RE.match(txt)
            if m:
                entries.append(TopEntry(side, category, rank, m.group("name"), m.group("squad"), int(m.group("value"))))
        else:
            m = _TOP_SQUAD_RE.match(txt)
            if m:
                entries.append(TopEntry(side, category, rank, m.group("name"), None, int(m.group("value"))))
    return entries


def parse_fso_event(html: str, event_id: int) -> FSOEvent | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")

    h3 = soup.find("h3")
    frame_title = _text(h3)
    if not frame_title:
        return None

    event = FSOEvent(event_id=event_id, frame_title=frame_title)
    seen_sides: dict[str, int] = {}

    for side_header in soup.find_all("h2"):
        side = _text(side_header)
        # Side names are per-event, not a fixed pair: as well as
        # Allies/Axis, frames use themed names like USAAF/Luftwaffe or
        # Blue Thunder. What identifies a side header is its position -
        # it sits inside the side's own table, whereas page furniture
        # ("Event Logs", "CB Login") does not.
        table = side_header.find_parent("table")
        if table is None or not side:
            continue

        # Some frames were submitted with both sides labelled the same
        # (event 2168 titles both "Allies"). They're still two distinct
        # teams with their own squads and totals, so keep both and make
        # the later one's name unique rather than merging two opposing
        # sides together or dropping one.
        seen_sides[side] = seen_sides.get(side, 0) + 1
        if seen_sides[side] > 1:
            side = f"{side} ({seen_sides[side]})"

        current_squad = None
        current_pilot = None
        seen_pilots = set()  # (side, pilot) already added to participants
        pilot_seq = {}  # (side, pilot) -> next seq number, keyed so a pilot
        # who disconnects and reconnects under the same name (a second
        # pilot_row block for them later in the same event) continues the
        # sequence instead of colliding with their earlier log entries.

        for row in table.find_all("tr", recursive=False):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            cell = cells[0]
            bgcolor = (cell.get("bgcolor") or row.get("bgcolor") or "").lower()

            if cell.find("h2"):
                continue  # the "Allies"/"Axis" title row itself

            if bgcolor == "#877634":
                current_squad = _text(cell).rstrip()
                continue

            if cell.get("class") and "pilot_row" in cell.get("class"):
                current_pilot = _text(cell)
                key = (side, current_pilot)
                if key not in seen_pilots:
                    seen_pilots.add(key)
                    pilot_seq[key] = 0
                    event.participants.append(Participant(side=side, squad=current_squad or "", pilot=current_pilot))
                # else: pilot reconnected under the same name within this
                # event - keep their original squad and continue the seq
                # counter below rather than re-adding/duplicating them.
                continue

            if bgcolor == "#b34b4b":
                nested = cell.find("table")
                if nested is not None:
                    event.squad_stats.append(_parse_group_stats_table(nested, side, current_squad))
                continue

            if bgcolor == "#9c9c9c":
                nested = cell.find("table")
                if nested is not None:
                    event.side_stats.append(_parse_group_stats_table(nested, side, None))
                    event.top_entries.extend(_parse_top_entries(nested, side))
                continue

            # plain log line
            txt = _text(cell)
            m = _LOG_LINE_RE.match(txt)
            if m and current_pilot:
                timestamp, desc = m.groups()
                classified = _classify_log_line(desc)
                key = (side, current_pilot)
                pilot_seq[key] = pilot_seq.get(key, 0) + 1
                event.log_entries.append(LogEntry(
                    side=side, pilot=current_pilot, seq=pilot_seq[key], timestamp=timestamp,
                    raw_text=desc, **classified,
                ))

    # A page with a frame title but no sides parsed isn't a successful
    # read - it means the layout didn't match. Returning the empty event
    # would let the caller store a frame_title and mark it fetched, so it
    # would never be retried; 184 events were lost that way when side
    # names outside Allies/Axis went unrecognised. Report it as a failure
    # instead, so it stays in the retry set and shows up in the log.
    if not event.participants and not event.log_entries:
        return None

    _disambiguate_squads(event)
    return event


def _disambiguate_squads(event: FSOEvent) -> None:
    """Make (side, squad) unique across an event's squad stats.

    squad_stats is keyed on (event_id, side, squad), so a repeated squad
    within one side aborts the insert - and because the scrape saves as
    it goes, one malformed page used to take the whole run down with it.
    Suffixing keeps both blocks rather than dropping one or summing them
    into a total the page never claimed."""
    seen: dict[tuple[str, str], int] = {}
    for stats in event.squad_stats:
        key = (stats.side, stats.squad or "")
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            stats.squad = f"{stats.squad} ({seen[key]})"
