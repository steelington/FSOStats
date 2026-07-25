"""One pilot's career across 18 years of Friday Squad Operations.

Attributing a kill to the aircraft the pilot was flying
-------------------------------------------------------
A `kill` row's `plane` is the *victim's* aircraft - only `sortie_start`
says what the pilot themselves took off in. Counting planes across all
actions is the classic misread of this data and makes both sides look
identical (see CLAUDE.md).

But a pilot's log within one frame is a clean chronological sequence:

    sortie_start  N1K2-J
    kill          Tu-2S      <- victim's aircraft
    landed
    sortie_start  Ki-84-Ia
    kill          La-5FN

so every entry belongs to the most recent `sortie_start` at or before
its `seq`. That recovers what the pilot was actually flying for each
kill and death, which is what makes a per-aircraft K/D meaningful.
Checked against five recent frames: 1,001 kills, none of them without a
preceding sortie.

Handles fold case throughout, as in headtohead - 165 differ from another
only by capitalisation.
"""
from __future__ import annotations

# Shown for entries with no preceding sortie_start to attribute to.
UNATTRIBUTED = "(no sortie recorded)"

# Every log entry for one pilot, carrying the aircraft they were flying
# at the time. `seq <= l.seq` so a sortie_start maps to itself.
#
# excluded = 0 keeps the duplicate frames (2168/2169, 2756/2757) from
# counting twice.
_ATTRIBUTED = """
    SELECT l.event_id, l.side, l.seq, l.action, l.plane, l.other_pilot, l.detail,
           (SELECT s.plane FROM log_entries s
             WHERE s.event_id = l.event_id AND s.side = l.side AND s.pilot = l.pilot
               AND s.action = 'sortie_start' AND s.seq <= l.seq
             ORDER BY s.seq DESC LIMIT 1) AS flown
    FROM log_entries l
    JOIN events e ON e.event_id = l.event_id AND e.excluded = 0
    WHERE l.pilot = :pilot COLLATE NOCASE
"""


def career(db, pilot: str) -> dict:
    """Headline totals for one pilot.

    `captured`, `crashed` and `exploded` are how a death ended, so they
    are a breakdown *of* deaths rather than additions to them - a death
    with no detail is a plain shootdown.
    """
    row = db.execute(f"""
        WITH mine AS ({_ATTRIBUTED})
        SELECT
          (SELECT COUNT(DISTINCT p.event_id) FROM participants p
             JOIN events e ON e.event_id = p.event_id AND e.excluded = 0
            WHERE p.pilot = :pilot COLLATE NOCASE)                      AS frames,
          SUM(action = 'sortie_start')                                  AS sorties,
          SUM(action = 'kill')                                          AS kills,
          SUM(action = 'ground_kill')                                   AS ground_kills,
          SUM(action = 'death')                                         AS deaths,
          SUM(action = 'landed')                                        AS landed,
          SUM(action = 'bailed')                                        AS bailed,
          SUM(action = 'ditched')                                       AS ditched,
          SUM(action = 'death' AND detail = 'captured')                 AS captured,
          SUM(action = 'death' AND detail = 'crashed')                  AS crashed,
          SUM(action = 'death' AND detail = 'exploded')                 AS exploded,
          COUNT(DISTINCT flown)                                         AS aircraft_types
        FROM mine
    """, {"pilot": pilot}).fetchone()

    stats = {k: (row[i] or 0) for i, k in enumerate(
        ("frames", "sorties", "kills", "ground_kills", "deaths", "landed",
         "bailed", "ditched", "captured", "crashed", "exploded", "aircraft_types"))}
    stats["kd"] = stats["kills"] / stats["deaths"] if stats["deaths"] else float(stats["kills"])
    # Frames and FSOs are both units users think in: an FSO campaign is
    # normally three frames on consecutive Fridays.
    row = db.execute("""
        SELECT MIN(substr(e.event_date,1,10)), MAX(substr(e.event_date,1,10)),
               COUNT(DISTINCT e.campaign)
        FROM participants p JOIN events e ON e.event_id = p.event_id AND e.excluded = 0
        WHERE p.pilot = :pilot COLLATE NOCASE""", {"pilot": pilot}).fetchone()
    stats["first_frame"], stats["last_frame"] = row[0], row[1]
    stats["campaigns"] = row[2] or 0
    return stats


def aircraft(db, pilot: str) -> list[dict]:
    """Aircraft the pilot flew, with the record in each.

    Sorties come from `sortie_start`; kills and deaths are attributed to
    the sortie they fell within, so these are the pilot's own aircraft
    throughout - never the victim's.
    """
    rows = db.execute(f"""
        WITH mine AS ({_ATTRIBUTED})
        SELECT flown,
               SUM(action = 'sortie_start') AS sorties,
               SUM(action = 'kill')         AS kills,
               SUM(action = 'death')        AS deaths,
               SUM(action = 'ground_kill')  AS ground_kills
        FROM mine
        GROUP BY flown
        ORDER BY sorties DESC, kills DESC
    """, {"pilot": pilot})
    out = []
    for plane, sorties, kills, deaths, ground in rows:
        out.append({
            # A null bucket is real: about 4% of pilot-frames have no
            # sortie_start before their first entry, so those kills
            # cannot be pinned to an aircraft. Showing them keeps this
            # table summing to the career totals instead of quietly
            # falling short.
            "plane": plane or UNATTRIBUTED,
            "sorties": sorties or 0,
            "kills": kills or 0,
            "deaths": deaths or 0,
            "ground_kills": ground or 0,
            "kd": (kills or 0) / deaths if deaths else float(kills or 0),
        })
    return out


def frames(db, pilot: str) -> list[dict]:
    """Every frame the pilot flew, newest first, with their result in it.

    `side` is the label the event page carried. Those are unreliable -
    themed names, occasional duplicates, a few swapped outright - so it
    is shown as recorded rather than treated as meaning anything. The
    inferred role lives in sides.py and isn't persisted yet.
    """
    # Side and squad are fetched separately and merged below rather than
    # joined here. A CTE has no index, so joining the pilot's ~541
    # participant rows to their ~5,500 log rows made SQLite rescan the
    # former for each of the latter - 3M comparisons, 12.3s, against
    # 0.02s for the aggregate on its own. Correlating a subquery per
    # frame instead costs 2.4s. Two small queries and a dict beat both.
    rows = db.execute(f"""
        WITH mine AS ({_ATTRIBUTED})
        SELECT e.event_id,
               substr(e.event_date, 1, 10)        AS date,
               COALESCE(e.display_title, e.title) AS frame,
               SUM(m.action = 'sortie_start')     AS sorties,
               SUM(m.action = 'kill')             AS kills,
               SUM(m.action = 'death')            AS deaths,
               SUM(m.action = 'ground_kill')      AS ground_kills,
               GROUP_CONCAT(DISTINCT CASE WHEN m.action = 'sortie_start'
                                          THEN m.plane END) AS planes
        FROM mine m
        JOIN events e ON e.event_id = m.event_id
        GROUP BY e.event_id
        ORDER BY e.event_date DESC
    """, {"pilot": pilot})

    # `role` is inferred from the aircraft the team flew (sides.py,
    # 97.9% accurate) and is the trustworthy one; `side` is the label
    # the submitter typed. Both are returned so the UI can show the
    # inference without hiding the source.
    sides = {
        r[0]: (r[1] or "", r[2] or "", r[3] or "")
        for r in db.execute(
            "SELECT p.event_id, p.side, p.squad, r.role FROM participants p "
            "LEFT JOIN side_roles r ON r.event_id = p.event_id AND r.side = p.side "
            "WHERE p.pilot = :pilot COLLATE NOCASE", {"pilot": pilot})
    }
    blank = ("", "", "")
    return [
        {"event_id": r[0], "date": r[1], "frame": r[2],
         "side": sides.get(r[0], blank)[0],
         "squad": sides.get(r[0], blank)[1],
         "role": sides.get(r[0], blank)[2],
         "sorties": r[3] or 0, "kills": r[4] or 0,
         "deaths": r[5] or 0, "ground_kills": r[6] or 0,
         "planes": (r[7] or "").replace(",", ", ")}
        for r in rows
    ]


def yearly(db, pilot: str) -> list[tuple[str, int, int, int]]:
    """(year, frames, sorties, kills) for the trend chart."""
    rows = db.execute(f"""
        WITH mine AS ({_ATTRIBUTED})
        SELECT substr(e.event_date, 1, 4) AS yr,
               COUNT(DISTINCT e.event_id),
               SUM(m.action = 'sortie_start'),
               SUM(m.action = 'kill')
        FROM mine m
        JOIN events e ON e.event_id = m.event_id
        GROUP BY yr ORDER BY yr
    """, {"pilot": pilot})
    return [(r[0], r[1] or 0, r[2] or 0, r[3] or 0) for r in rows]
