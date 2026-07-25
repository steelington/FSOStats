"""All-time boards.

Two different questions, and `top_entries` only answers the second.

**Totals** are counted from the logs (`log_entries` for pilots,
`squad_stats` for squads). `top_entries` cannot give them: it holds only
each frame's leaders - seven deep for pilots, three for squads - so
summing it would total the podium, not the career, and would flatter a
pilot who was often second over one who was steadily fourth.

**Podium finishes** are what `top_entries` is for: how often someone
topped a board at all.

`rank` is per side, not per frame - (event, side, category, rank) is
unique, so every frame has two rank-1 pilots, one per team. A "win"
here therefore means topping your own side's board.

Categories: pilot_kills, pilot_obj, squad_kills, squad_obj.
"""
from __future__ import annotations

from fsostats.squads import squad_key

# Excluded events are filtered everywhere: the duplicate frames would
# otherwise count twice, and their pilots twice over.


def pilot_totals(db, metric: str = "kills", limit: int = 200) -> list[dict]:
    """All-time pilot board, counted from the log.

    metric: "kills" (air) or "ground_kills".
    """
    action = {"kills": "kill", "ground_kills": "ground_kill"}[metric]
    rows = db.execute("""
        SELECT l.pilot, COUNT(*) AS total
        FROM log_entries l
        JOIN events e ON e.event_id = l.event_id AND e.excluded = 0
        WHERE l.action = ?
        GROUP BY l.pilot COLLATE NOCASE
        ORDER BY total DESC
        LIMIT ?
    """, (action, limit)).fetchall()

    # Frames *flown* rather than frames scored in - counting distinct
    # event_id on the kill rows above would miss every frame the pilot
    # flew without scoring, inflating the per-frame average. Fetched
    # separately and merged, since a correlated subquery per pilot is
    # far slower.
    flown = {
        r[0].casefold(): r[1]
        for r in db.execute("""
            SELECT p.pilot, COUNT(DISTINCT p.event_id)
            FROM participants p
            JOIN events e ON e.event_id = p.event_id AND e.excluded = 0
            GROUP BY p.pilot COLLATE NOCASE""")
    }
    out = []
    for pilot_name, total in rows:
        frames = flown.get(pilot_name.casefold(), 0)
        out.append({
            "pilot": pilot_name, "total": total, "frames": frames,
            "per_frame": total / frames if frames else 0.0,
        })
    return out


def squad_totals(db, metric: str = "kills", limit: int = 200) -> list[dict]:
    """All-time squad board, from the posted per-frame squad totals."""
    column = {"kills": "kills", "ground_kills": "obj_destroyed"}[metric]
    rows = db.execute(f"""
        SELECT s.squad, SUM(s.{column}), COUNT(DISTINCT s.event_id)
        FROM squad_stats s
        JOIN events e ON e.event_id = s.event_id AND e.excluded = 0
        WHERE s.squad <> ''
        GROUP BY s.squad
    """).fetchall()

    merged: dict[str, dict] = {}
    for squad, total, frames in rows:
        key = squad_key(squad)
        entry = merged.setdefault(key, {"squad": squad, "total": 0, "frames": 0, "_best": 0})
        entry["total"] += total or 0
        entry["frames"] += frames or 0
        # Keep the spelling used in the most frames as the display name.
        if (frames or 0) > entry["_best"]:
            entry["_best"], entry["squad"] = frames or 0, squad

    out = []
    for entry in merged.values():
        entry.pop("_best")
        entry["per_frame"] = entry["total"] / entry["frames"] if entry["frames"] else 0.0
        out.append(entry)
    out.sort(key=lambda s: (-s["total"], s["squad"].casefold()))
    return out[:limit]


def podium(db, category: str, limit: int = 200) -> list[dict]:
    """How often each name topped its side's board, and reached the top 3.

    category: pilot_kills | pilot_obj | squad_kills | squad_obj
    """
    rows = db.execute("""
        SELECT t.name,
               SUM(t.rank = 1) AS wins,
               SUM(t.rank <= 3) AS top3,
               COUNT(*) AS placings,
               MAX(t.value) AS best
        FROM top_entries t
        JOIN events e ON e.event_id = t.event_id AND e.excluded = 0
        WHERE t.category = ? AND t.name <> ''
        GROUP BY t.name COLLATE NOCASE
        ORDER BY wins DESC, top3 DESC, placings DESC
        LIMIT ?
    """, (category, limit)).fetchall()
    return [
        {"name": r[0], "wins": r[1] or 0, "top3": r[2] or 0,
         "placings": r[3] or 0, "best": r[4] or 0}
        for r in rows
    ]
