"""Grouping frames into campaigns.

One `events` row is one frame. An FSO campaign is normally three frames
on consecutive Fridays, but nothing scraped from ahevents.net links them:
the only signals are the title and the date.

Most titles carry their frame number, in one of several spellings. The
rest are corrected by hand in OVERRIDES below.

**Corrections must not be written back to `events.title`.** Every scrape
re-parses the whole landing page and hands all of it to
`upsert_event_listings`, which does `title=excluded.title` for every row.
Only the log *fetch* is incremental, so any edit to `events.title` is
reverted the next time the update runs, including the in-app Update
button. The overrides therefore live here, in code, and the raw scraped
title stays exactly as the site served it.
"""
from __future__ import annotations

import re

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
}
ROMAN_NUMBERS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}

# Ordered: the digit form is the most common and the least ambiguous, so
# it wins when a title somehow matches more than one.
_FRAME_PATTERNS = (
    # "Frame 3", "Fr. 3", "Frame #3", "FRAME3", "OUTOFTHESUNFrame3",
    # and the leading form "Frame 1: Operation Bodenplatte".
    (re.compile(r"(?:frame|fr)\.?\s*#?\s*(\d+)", re.I),
     lambda m: int(m.group(1))),
    # "Frame One", "Frame Three" - the house style from about 2017 on.
    (re.compile(r"(?:frame|fr)\.?\s*(" + "|".join(WORD_NUMBERS) + r")\b", re.I),
     lambda m: WORD_NUMBERS[m.group(1).lower()]),
    # "Frame I", "Frame III". Anchored on both sides so "Frame Is ..."
    # or a stray "fr in" can't match.
    (re.compile(r"(?:frame|fr)\.?\s*(i{1,3}|iv|vi?)\b", re.I),
     lambda m: ROMAN_NUMBERS[m.group(1).lower()]),
)

MAX_FRAME_NO = 6  # nothing legitimate goes past this; a bigger number is a year


def frame_number_from_title(title: str) -> int | None:
    """The frame number a title states, or None if it states none.

    Covers 537 of the 587 frames that have logs (91.5%); the remainder
    are in OVERRIDES.
    """
    for pattern, convert in _FRAME_PATTERNS:
        m = pattern.search(title)
        if not m:
            continue
        try:
            value = convert(m)
        except KeyError:      # a spelling the table doesn't hold
            continue
        if 1 <= value <= MAX_FRAME_NO:
            return value
    return None


_FRAME_MARKER = re.compile(
    r"(?:frame|fr)\.?\s*#?\s*(?:\d+|" + "|".join(WORD_NUMBERS) + r"|i{1,3}|iv|vi?)\b",
    re.I,
)
# "JAN FSO -", "March 2018 FSO:", "MAY 2015 FSO -", "July 2019 FSO."
_DATE_PREFIX = re.compile(
    r"^\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*"
    r"\d{0,4}\s*fso\s*[-:.]?\s*",
    re.I,
)


def campaign_key(title: str) -> str:
    """A title reduced to something comparable across frames.

    Frames of one campaign are retitled freely between weeks - case,
    punctuation, spelling and word order all drift - so the key strips
    everything that isn't a letter or digit.
    """
    text = _DATE_PREFIX.sub(" ", title)
    text = _FRAME_MARKER.sub(" ", text)
    text = re.sub(r"\blogs?\b", " ", text, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", text.lower())


# --- hand corrections -------------------------------------------------
#
# event_id -> (campaign, frame_no, note)
#
# `campaign` is the canonical name for the campaign the frame belongs to;
# frames sharing it are one campaign regardless of what their titles say.
# `frame_no` of None means a genuine one-off that is its own campaign.
#
# Derived by reading each unnumbered frame's neighbours in date order.
# The dominant case by far is a frame 1 that omitted the number while its
# own frames 2 and 3 are numbered normally - but see CASE BLUE, Kursk and
# Tunisia, where the unnumbered frame is in the *middle* of the run. Do
# not assume unnumbered means frame 1.
OVERRIDES: dict[int, tuple[str, int | None, str]] = {
    # --- unnumbered frame 1, frames 2-3 numbered ---
    1586: ("Angels Eight", 1, "frames 2-3 are among the 73 logless events"),
    2081: ("Alder Krieg", 1, "frame 2 spells it 'Alder Kreig'"),
    2102: ("Normandy Summer 1944", 1, "frames 2-3 titled 'Normandy Summer'"),
    2199: ("Battle of Britain 1940 (May 2012)", 1, "name reused in 2014 and 2016"),
    2382: ("Into the Tigers Den", 1, "title carries a 'JAN FSO -' prefix"),
    2488: ("Crimean Catastrophe", 1, ""),
    2787: ("Dragonslayers", 1, "frames 2-3 spell it as one word"),
    2897: ("Battle for Salerno 1943", 1, "frames 2-3 misspell it 'SELARNO'"),
    3343: ("Malta 1942", 1, "frame 2 reorders it to 'FSO FRAME 2: 1942 MALTA'"),
    3928: ("The Last Hop: Okinawa 1945", 1, ""),
    3993: ("All to Little Purpose - Channel Front 1941", 1,
           "frame 2 spells it 'All too Little Purpose'"),
    4069: ("Southern Penetration", 1, ""),
    4130: ("Over the Reich", 1, ""),
    4189: ("Battle of Okinawa", 1, ""),
    4235: ("Last Days of the Luftwaffe", 1, ""),
    4257: ("Operation Clarion 1945", 1, "frame 2 drops 'Operation'"),

    # --- unnumbered frame in the MIDDLE of a numbered run ---
    3268: ("Case Blue", 2,
           "sits between 'Case Blue Frame 1' (3267) and 'Frame 3' (3270)"),
    3604: ("Greatest Air Battles: Kursk 1943", 2,
           "sits between frames 1 (3603) and 3 (3605)"),
    3938: ("Tunisia: The Verdun of the Mediterranean 1943", 2,
           "frame 1 (3935) is numbered; word order differs between frames"),
    3941: ("Tunisia: The Verdun of the Mediterranean 1943", 3, ""),
    3915: ("Germanys Last Stand - North Sea 1945", 2,
           "frame 1 (3913) is numbered but types the year as 1954"),
    3916: ("Germanys Last Stand - North Sea 1945", 3, ""),

    # --- whole campaigns with no frame numbers anywhere ---
    3018: ("Gates of Warsaw", 1, "all three frames unnumbered, case varies"),
    3020: ("Gates of Warsaw", 2, ""),
    3023: ("Gates of Warsaw", 3, ""),
    3529: ("Stalemate at Anzio", 1, "all three unnumbered under a 'July 2019 FSO' prefix"),
    3531: ("Stalemate at Anzio", 2, ""),
    3533: ("Stalemate at Anzio", 3, ""),
    3731: ("Operation Cherryblossom: Bougainville 43", 1, "all three unnumbered"),
    3732: ("Operation Cherryblossom: Bougainville 43", 2, ""),
    3734: ("Operation Cherryblossom: Bougainville 43", 3,
           "labels are swapped at source - see FINDINGS.md"),
    3849: ("Grapple for the Gulf 1942", 1, "all three unnumbered"),
    3852: ("Grapple for the Gulf 1942", 2, ""),
    3855: ("Grapple for the Gulf 1942", 3, ""),
    4251: ("Gallands Gambit: Operation Donnerkeil", 1,
           "frames 2-3 append 'Operation Donnerkeil'"),
    4253: ("Gallands Gambit: Operation Donnerkeil", 2, ""),
    4255: ("Gallands Gambit: Operation Donnerkeil", 3, ""),

    # --- unnumbered frames 1-2, frame 3 numbered ---
    4204: ("The Push West - A Russian Winter", 1, "frame 3 (4209) is numbered"),
    4206: ("The Push West - A Russian Winter", 2, ""),
    4228: ("Mountbatten Melee 42", 1, "frame 3 (4232) is numbered"),
    4230: ("Mountbatten Melee 42", 2, ""),

    # --- renamed mid-campaign; see UNCERTAIN below ---
    4140: ("Thunder over the Solomons", 1, "campaign renamed after frame 1"),
    4142: ("Thunder over the Solomons", 2, "titled 'Bougainville 43'"),
    4145: ("Thunder over the Solomons", 3, "titled 'Bougainville 43'"),

    # --- genuine one-offs, not part of any campaign ---
    2168: ("North Sea Raiders", None,
           "standalone frame between two campaigns; resubmitted as 2169"),
    2596: ("Combat Challenge III", None, "standalone event"),
}

# Events to keep out of the frame list and out of every total.
EXCLUDED: dict[int, str] = {
    2169: "duplicate submission of 2168 - same date, same 1223 log entries",
    2756: "duplicate submission of 2757 - same date, same 1234 log entries; "
          "2757 is the copy that carries 'Frame 1'",
    3049: "'Training' - a 14-entry test event, not an FSO frame",
    1495: "'SEA crash test / B17 fight' - a test event run mid-campaign, "
          "between Operation Marita frames 1 and 2",
}

# Judgement calls worth revisiting against the event write-ups on
# ahevents.net, recorded rather than buried:
#
#   4140/4142/4145 - "Thunder over the Solomons" (10 Jan 2025) then
#   "Bougainville 43" on the next two Fridays. Bougainville is in the
#   Solomons and the three sit on consecutive Fridays with no other
#   candidates either side, so they are treated as one renamed campaign.
#   The alternative reading is a one-off followed by a two-frame
#   campaign. Note "Bougainville 43" also names the unrelated 2021
#   Cherryblossom campaign, four years earlier.
#
#   2168/2169 - "North Sea Raiders", both on 30 Mar 2012 with identical
#   log counts. Treated as one standalone frame plus a duplicate rather
#   than two frames of a campaign: the Fridays either side belong to
#   other campaigns that are already complete.
UNCERTAIN = (4140, 4142, 4145, 2168, 2169)


def resolve(event_id: int, title: str) -> tuple[str | None, int | None, bool]:
    """Return (campaign_name, frame_no, excluded) for one event.

    campaign_name is None when the title alone has to carry it - the
    caller clusters those by campaign_key() and date. A hand-corrected
    event returns its campaign outright and needs no clustering.
    """
    if event_id in EXCLUDED:
        return None, None, True
    if event_id in OVERRIDES:
        campaign, frame_no, _note = OVERRIDES[event_id]
        return campaign, frame_no, False
    return None, frame_number_from_title(title), False
