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

import difflib
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


# The `\.\d+` tail catches "Frame 3.1" / "Frame 3.2", which is how one
# frame posted as two rows - one side each - is numbered. Without it the
# stripped title keeps a stray ".1".
_FRAME_MARKER = re.compile(
    r"(?:frame|fr)\.?\s*#?\s*(?:\d+(?:\.\d+)?|"
    + "|".join(WORD_NUMBERS) + r"|i{1,3}|iv|vi?)\b",
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

    # --- renamed mid-campaign (confirmed by the user, 25 Jul 2026) ---
    # Frames 2-3 are titled "Bougainville 43"; the campaign name is the
    # one frame 1 carries. Not to be confused with the unrelated 2021
    # Cherryblossom campaign, which also fought over Bougainville.
    4140: ("Thunder over the Solomons", 1, "campaign renamed after frame 1"),
    4142: ("Thunder over the Solomons", 2, "titled 'Bougainville 43'"),
    4145: ("Thunder over the Solomons", 3, "titled 'Bougainville 43'"),

    # --- renamed mid-campaign, so no title match can group them ---
    # Found by validation: each came out as a campaign numbered [1, 3]
    # with its middle frame stranded as a singleton under another name.
    2935: ("Battle of Britain 1940 (Sep 2015)", 1, "name reused in 2012, 2014 and 2016"),
    2937: ("Battle of Britain 1940 (Sep 2015)", 2, "titled 'September FSO - B.O.B 1940 Frame 2'"),
    2938: ("Battle of Britain 1940 (Sep 2015)", 3, ""),
    2982: ("Bougainville 1943", 1, ""),
    2985: ("Bougainville 1943", 2, "titled 'PRIVATEERS Solomons 1943 Frame 2'"),
    2986: ("Bougainville 1943", 3, ""),

    # --- frame numbered wrongly at source ---
    # 3774 and 3776 are both titled "Frame 2", a week apart, with
    # different logs (692 entries against 178). 3776 is the third frame.
    3776: ("Crossing the Dnieper: Cherkassy-Korsun 1943", 3,
           "titled 'Frame 2' at source, a week after the real frame 2 (3774)"),

    # --- genuine one-offs, not part of any campaign ---
    2169: ("North Sea Raiders", None,
           "standalone frame between two campaigns; 2168 is the same "
           "submission with both teams mislabelled 'Allies'"),
    2596: ("Combat Challenge III", None, "standalone event"),
}

# Events to keep out of the frame list and out of every total.
EXCLUDED: dict[int, str] = {
    2168: "duplicate submission of 2169, and the worse copy of the two: its "
          "log entries are byte-identical to 2169's, but it labels both "
          "teams 'Allies' (the parser suffixes the repeat) where 2169 has "
          "proper Allies/Axis labels",
    2756: "duplicate submission of 2757 - same date, same 1234 log entries; "
          "2757 is the copy that carries 'Frame 1'",
    3049: "'Training' - a 14-entry test event, not an FSO frame",
    1495: "'SEA crash test / B17 fight' - a test event run mid-campaign, "
          "between Operation Marita frames 1 and 2",
}

# Nothing is currently unresolved. 2168/2169 was the last open case and
# was settled by comparing the logs directly: all 1223 entries match
# byte for byte across the two, same pilots killing the same victims in
# the same aircraft, so it is one frame submitted twice and not two
# frames sharing a title.
UNCERTAIN: tuple[int, ...] = ()


# --- clustering frames into campaigns --------------------------------
#
# Frames run weekly, so two frames of one campaign are ~7 days apart.
# 15 days allows for one skipped Friday - FSO takes breaks - without
# reaching across to the next campaign. The frame-number constraint
# below is what makes the wider window safe.
MAX_GAP_DAYS = 15

# Similarity needed to call two titles the same campaign. Tuned by
# sweeping against the numbered frames: see cluster() for the method.
SIMILARITY = 0.55

# Words carrying no campaign identity.
_STOP_WORDS = {"logs", "log", "fso", "the", "a", "of", "frame", "fr"}


def campaign_tokens(title: str) -> set[str]:
    """The meaningful words of a title.

    Token overlap is what survives word-order reversal - 'Tunisia:
    Verdun of the Mediterranean 1943' against '1943: Tunisia - The
    Verdun of Mediterranean' - which sequence matching cannot.
    """
    text = _DATE_PREFIX.sub(" ", title)
    text = _FRAME_MARKER.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return {w for w in text.split() if len(w) > 1 and w not in _STOP_WORDS}


def title_similarity(a_tokens: set[str], a_key: str,
                     b_tokens: set[str], b_key: str) -> float:
    """How alike two frame titles are, 0..1."""
    sequence = difflib.SequenceMatcher(None, a_key, b_key).ratio()
    if not a_tokens or not b_tokens:
        return sequence
    shared = len(a_tokens & b_tokens)
    # Overlap over the smaller set, so 'Case Blue' still matches
    # 'Case Blue: the drive on Stalingrad'. Discounted slightly so a
    # true sequence match outranks a bare subset.
    overlap = shared / min(len(a_tokens), len(b_tokens)) * 0.95
    jaccard = shared / len(a_tokens | b_tokens)
    return max(overlap, jaccard, sequence)


def cluster(frames: list[dict]) -> list[list[dict]]:
    """Group frames into campaigns. `frames` must be date-ordered dicts
    with keys: id, date (datetime.date), title, frame_no, campaign.

    A frame joins an open campaign when the titles are similar enough,
    the gap is short enough, and - the part that makes it reliable - the
    frame number isn't already taken. Frame numbers are ground truth for
    91.5% of frames, so using them as a constraint during clustering,
    not only to check it afterwards, prevents two campaigns merging.

    The exception is two events on the same date: those are one frame
    posted as two rows, one side each (3540/3541), not two frames.

    Hand-curated campaign names win outright. Nothing else could group
    'Thunder over the Solomons' with the 'Bougainville 43' frames that
    follow it, because the campaign was renamed mid-run.

    Measured this way over the 583 frames with logs: 208 campaigns, 183
    of them a complete three, no broken frame-number sequences.
    """
    campaigns: list[dict] = []
    by_curated: dict[str, dict] = {}

    for frame in frames:
        curated = frame.get("campaign")
        if curated:
            existing = by_curated.get(curated)
            if existing is not None:
                existing["frames"].append(frame)
                existing["last"] = frame["date"]
                continue

        tokens = campaign_tokens(frame["title"])
        key = campaign_key(frame["title"])

        best, best_score = None, 0.0
        for camp in reversed(campaigns):
            if (frame["date"] - camp["last"]).days > MAX_GAP_DAYS:
                continue
            if curated and camp.get("curated") and camp["curated"] != curated:
                continue
            number = frame.get("frame_no")
            if number and number in camp["numbers"]:
                # Same date means one frame split by side, not a clash.
                if frame["date"] not in camp["dates"]:
                    continue
            score = title_similarity(tokens, key, camp["tokens"], camp["key"])
            if score > best_score:
                best, best_score = camp, score

        if best is not None and best_score >= SIMILARITY:
            best["frames"].append(frame)
            best["last"] = frame["date"]
            best["tokens"] |= tokens
            best["dates"].add(frame["date"])
            if frame.get("frame_no"):
                best["numbers"].add(frame["frame_no"])
            if curated:
                best["curated"] = curated
                by_curated[curated] = best
            continue

        fresh = {
            "key": key, "tokens": set(tokens), "last": frame["date"],
            "dates": {frame["date"]}, "curated": curated,
            "numbers": {frame["frame_no"]} if frame.get("frame_no") else set(),
            "frames": [frame],
        }
        campaigns.append(fresh)
        if curated:
            by_curated[curated] = fresh

    return [c["frames"] for c in campaigns]


def campaign_name(frames: list[dict]) -> str:
    """The name to show for a clustered campaign.

    A curated name wins. Otherwise the longest title in the group with
    its frame marker stripped - the fullest spelling, since frames get
    abbreviated as a campaign goes on ('Operation Clarion 1945' then
    'Clarion - 1945').
    """
    for frame in frames:
        if frame.get("campaign"):
            return frame["campaign"]
    titles = []
    for frame in frames:
        text = _DATE_PREFIX.sub(" ", frame["title"])
        text = _FRAME_MARKER.sub(" ", text)
        # Submitters often end a title with 'logs' ("The Luftwaffe end is
        # near Frame 2 logs"), which is about the posting, not the
        # campaign, and would otherwise ride along in the name.
        text = re.sub(r"\blogs?\b", " ", text, flags=re.I)
        titles.append(text)
    best = re.sub(r"\s+", " ", max(titles, key=len)).strip()
    # Punctuation at either end is left over from stripping the frame
    # marker - "Frame 1: Operation Bodenplatte" would otherwise become
    # ": Operation Bodenplatte".
    return re.sub(r"^[\s\-:;,.]+|[\s\-:;,.]+$", "", best)


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
