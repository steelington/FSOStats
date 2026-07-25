"""Who killed whom, across 18 years of Friday Squad Operations.

This is the feature AHSTATS can't do: the main-arena score pages report
totals, whereas an FSO log names both parties on every shootdown, so a
pilot's record against any individual opponent is recoverable.

How the log represents a shootdown
----------------------------------
A single shootdown can be written twice, once from each side's log:

    kill    pilot = the killer,  other_pilot = the victim,
            plane = *the victim's* aircraft
    death   pilot = the victim,  other_pilot = the killer

`kill` is the better source - all 93,137 rows name their victim, where
only 50,996 of 78,265 `death` rows name a killer (the rest are captures,
crashes and ack, which have no-one to credit).

But `kill` is not a superset. Of the deaths that do name a killer, 91.4%
have a matching kill row and 4,410 do not - a team's log can record being
shot down by someone whose own side's log never recorded the kill. So
this module takes every kill row, then adds only those death rows with no
matching kill, which is complete without counting the 46,586 overlapping
shootdowns twice.

Handles fold case
-----------------
165 handles differ from another only by capitalisation - 11DUKE11 and
11Duke11 are one pilot typed two ways. Every comparison here is COLLATE
NOCASE, backed by idx_log_pilot_nocase / idx_log_other_nocase; a plain
index cannot serve a NOCASE comparison.

Pilots who *renamed* are a separate, harder problem - AHSTATS solved it
with identity groups, and PLAN.md has porting that as an open item.
"""
from __future__ import annotations

from dataclasses import dataclass

# Every engagement involving one pilot, as (event_id, killer, victim,
# victim_plane). The two halves are the kill rows and the death rows
# that no kill row covers.
#
# `excluded = 0` matters: 2168/2169 and 2756/2757 are the same frame
# submitted twice, byte-identical, so counting both would double every
# engagement in them.
_ENGAGEMENTS = """
    SELECT k.event_id, k.pilot AS killer, k.other_pilot AS victim, k.plane AS victim_plane
    FROM log_entries k
    JOIN events e ON e.event_id = k.event_id AND e.excluded = 0
    WHERE k.action = 'kill' AND {kill_where}
  UNION ALL
    SELECT d.event_id, d.other_pilot AS killer, d.pilot AS victim, NULL AS victim_plane
    FROM log_entries d
    JOIN events e ON e.event_id = d.event_id AND e.excluded = 0
    WHERE d.action = 'death'
      AND d.other_pilot IS NOT NULL AND d.other_pilot <> ''
      AND {death_where}
      AND NOT EXISTS (
            SELECT 1 FROM log_entries k2
            WHERE k2.action = 'kill'
              AND k2.event_id = d.event_id
              AND k2.pilot = d.other_pilot COLLATE NOCASE
              AND k2.other_pilot = d.pilot COLLATE NOCASE)
"""


def _engagements_for(role: str) -> str:
    """The engagement set restricted to one pilot, by the role they play.

    Filtering inside the union rather than around it is what keeps this
    fast: each half then hits a NOCASE index instead of building every
    engagement in the database first.
    """
    if role == "killer":
        return _ENGAGEMENTS.format(
            kill_where="k.pilot = :pilot COLLATE NOCASE",
            death_where="d.other_pilot = :pilot COLLATE NOCASE",
        )
    return _ENGAGEMENTS.format(
        kill_where="k.other_pilot = :pilot COLLATE NOCASE",
        death_where="d.pilot = :pilot COLLATE NOCASE",
    )


@dataclass
class Opponent:
    """One opposing pilot and the record against them."""
    name: str
    killed: int       # times this pilot shot the opponent down
    killed_by: int    # times the opponent shot this pilot down
    frames: int       # frames the two both flew in

    @property
    def diff(self) -> int:
        return self.killed - self.killed_by

    @property
    def ratio(self) -> str:
        if not self.killed_by:
            return f"{self.killed}-0" if self.killed else "0-0"
        return f"{self.killed / self.killed_by:.2f}"


def victims(db, pilot: str, limit: int | None = None) -> list[tuple[str, int]]:
    """Pilots this one shot down, most often first."""
    sql = f"""
        WITH eng AS ({_engagements_for("killer")})
        SELECT victim, COUNT(*) AS n
        FROM eng
        WHERE victim IS NOT NULL AND victim <> ''
        GROUP BY victim COLLATE NOCASE
        ORDER BY n DESC, victim COLLATE NOCASE
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(r[0], r[1]) for r in db.execute(sql, {"pilot": pilot})]


def nemeses(db, pilot: str, limit: int | None = None) -> list[tuple[str, int]]:
    """Pilots who shot this one down, most often first."""
    sql = f"""
        WITH eng AS ({_engagements_for("victim")})
        SELECT killer, COUNT(*) AS n
        FROM eng
        WHERE killer IS NOT NULL AND killer <> ''
        GROUP BY killer COLLATE NOCASE
        ORDER BY n DESC, killer COLLATE NOCASE
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(r[0], r[1]) for r in db.execute(sql, {"pilot": pilot})]


def opponents(db, pilot: str, limit: int | None = None) -> list[Opponent]:
    """Every pilot this one traded with, both directions in one row.

    Ordered by total engagements, so the pilots someone actually fought
    repeatedly come first rather than the one-off encounters.
    """
    scored: dict[str, Opponent] = {}
    for name, n in victims(db, pilot):
        key = name.casefold()
        scored[key] = Opponent(name=name, killed=n, killed_by=0, frames=0)
    for name, n in nemeses(db, pilot):
        key = name.casefold()
        if key in scored:
            scored[key].killed_by = n
        else:
            scored[key] = Opponent(name=name, killed=0, killed_by=n, frames=0)

    shared = _shared_frame_counts(db, pilot, list(scored))
    for key, opponent in scored.items():
        opponent.frames = shared.get(key, 0)

    ordered = sorted(
        scored.values(),
        key=lambda o: (-(o.killed + o.killed_by), -o.killed, o.name.casefold()),
    )
    return ordered[:limit] if limit else ordered


def _shared_frame_counts(db, pilot: str, names: list[str]) -> dict[str, int]:
    """How many frames this pilot and each opponent both flew in.

    Without this, a 3-1 record reads the same whether it came from four
    meetings or forty.
    """
    if not names:
        return {}
    rows = db.execute(
        """
        WITH mine AS (
            SELECT DISTINCT p.event_id
            FROM participants p
            JOIN events e ON e.event_id = p.event_id AND e.excluded = 0
            WHERE p.pilot = :pilot COLLATE NOCASE
        )
        SELECT p.pilot, COUNT(DISTINCT p.event_id)
        FROM participants p
        JOIN mine ON mine.event_id = p.event_id
        WHERE p.pilot <> :pilot COLLATE NOCASE
        GROUP BY p.pilot COLLATE NOCASE
        """,
        {"pilot": pilot},
    )
    wanted = {n.casefold() for n in names}
    return {r[0].casefold(): r[1] for r in rows if r[0].casefold() in wanted}


def record_against(db, pilot: str, opponent: str) -> list[dict]:
    """Every engagement between two pilots, newest frame first.

    Each row is one shootdown: which way it went, in what frame, and the
    losing aircraft where the log names it (only kill rows carry it).
    """
    sql = f"""
        WITH killed AS ({_engagements_for("killer")}),
             died AS ({_engagements_for("victim")})
        SELECT ev.event_date, ev.event_id,
               COALESCE(ev.display_title, ev.title) AS frame,
               'killed' AS direction, killed.victim_plane AS plane
        FROM killed
        JOIN events ev ON ev.event_id = killed.event_id
        WHERE killed.victim = :opponent COLLATE NOCASE
      UNION ALL
        SELECT ev.event_date, ev.event_id,
               COALESCE(ev.display_title, ev.title) AS frame,
               'killed by' AS direction, died.victim_plane AS plane
        FROM died
        JOIN events ev ON ev.event_id = died.event_id
        WHERE died.killer = :opponent COLLATE NOCASE
        ORDER BY event_date DESC
    """
    return [
        {"date": r[0][:10], "event_id": r[1], "frame": r[2],
         "direction": r[3], "plane": r[4] or ""}
        for r in db.execute(sql, {"pilot": pilot, "opponent": opponent})
    ]


def summary(db, pilot: str, all_opponents: list[Opponent] | None = None) -> dict:
    """Headline head-to-head figures for one pilot.

    Pass an already-computed opponent list to avoid recomputing it - the
    shared-frame count behind it is the expensive part, and a caller
    showing both the summary and the table would otherwise pay twice.
    """
    if all_opponents is None:
        all_opponents = opponents(db, pilot)
    killed = sum(o.killed for o in all_opponents)
    killed_by = sum(o.killed_by for o in all_opponents)
    return {
        "kills": killed,
        "deaths": killed_by,
        "opponents": len(all_opponents),
        "ratio": (killed / killed_by) if killed_by else float(killed),
        "top_victim": max(all_opponents, key=lambda o: o.killed, default=None),
        "top_nemesis": max(all_opponents, key=lambda o: o.killed_by, default=None),
    }
