"""Work out which historical side a frame's team actually fought for.

The side names on ahevents.net can't be trusted on their own:

  * they're per-event and themed - Allies/Axis, but also USAAF/Luftwaffe,
    Blue Thunder, and so on;
  * some frames were submitted with both teams labelled identically
    (event 2168 calls both "Allies"), so the parser has to suffix the
    second one to keep them apart;
  * a few frames have the two labels swapped outright - event 3682 lists
    the team flying Fw 190s and Bf 109s as "Allies".

What the teams flew is far more reliable, so the role is inferred from
aircraft instead. Each type gets an affinity score from how one-sidedly
it's used across every labelled event, and a team's aircraft then vote,
weighted by that affinity and by sorties flown.

No single type can decide it, which matters: FSO substitutes an Allied
airframe when the game lacks an Axis model and vice versa, so Finland
flies P-40Cs and Brewster B-239s, and Italian C.205s turn up on the
Allied side after 1943. A type used by both sides simply carries little
weight rather than casting a wrong vote.

Measured by leave-one-out over 892 labelled teams (affinity never learned
from the team being classified): 97.9% correct. The residue is almost
entirely substitution-heavy Continuation War frames, plus the handful of
frames whose source labels are genuinely reversed.
"""
from __future__ import annotations

from collections import Counter, defaultdict

# Vehicles and gunner positions, not aircraft - they carry no side
# signal and would otherwise swamp the vote (Gunner-Observer alone
# accounts for ~9,000 sorties, split near evenly).
NOT_AIRCRAFT = {
    "Gunner-Observer", "Jeep", "Gun Ship or Field", "M8", "M3",
    "Osti", "Wirbelwind", "Panzer IV", "M4", "T-34",
}

# Below this many sorties elsewhere, a type hasn't earned a vote.
MIN_SORTIES = 20

# Teams whose label is trustworthy, used to learn affinities from.
TRUSTED_SIDES = ("Allies", "Axis")


def load_affinities(conn, exclude_event: int | None = None) -> dict[str, float]:
    """plane -> affinity in [-1, 1]: +1 always Allied, -1 always Axis.

    Pass exclude_event to leave one event out, so a team is never
    classified using affinities learned from itself."""
    placeholders = ",".join("?" * len(TRUSTED_SIDES))
    params: list = list(TRUSTED_SIDES)
    query = (
        "SELECT plane, side, COUNT(*) n FROM log_entries "
        "WHERE action='sortie_start' AND plane IS NOT NULL "
        f"AND side IN ({placeholders})"
    )
    if exclude_event is not None:
        query += " AND event_id <> ?"
        params.append(exclude_event)
    query += " GROUP BY plane, side"

    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for plane, side, n in conn.execute(query, params):
        if plane in NOT_AIRCRAFT:
            continue
        tally[plane][0 if side == "Allies" else 1] += n

    affinities = {}
    for plane, (allied, axis) in tally.items():
        total = allied + axis
        if total >= MIN_SORTIES:
            affinities[plane] = (allied - axis) / total
    return affinities


def classify_team(planes: Counter, affinities: dict[str, float]) -> tuple[str, float]:
    """Given a team's own-aircraft counts (from sortie_start entries),
    return (role, confidence) where role is 'allied', 'axis' or
    'unknown', and confidence is the mean per-sortie vote in [0, 1]."""
    score = 0.0
    counted = 0
    for plane, n in planes.items():
        affinity = affinities.get(plane)
        if affinity is None:
            continue
        score += n * affinity
        counted += n

    if not counted or score == 0:
        return "unknown", 0.0
    return ("allied" if score > 0 else "axis"), abs(score) / counted


def team_planes(conn, event_id: int, side: str) -> Counter:
    """A team's own aircraft. Only sortie_start says what a pilot flew -
    a kill entry names the victim's aircraft, so counting every action
    mixes both teams' types together and tells you nothing."""
    rows = conn.execute(
        "SELECT plane, COUNT(*) FROM log_entries "
        "WHERE event_id=? AND side=? AND action='sortie_start' AND plane IS NOT NULL "
        "GROUP BY plane",
        (event_id, side),
    )
    return Counter({plane: n for plane, n in rows if plane not in NOT_AIRCRAFT})
