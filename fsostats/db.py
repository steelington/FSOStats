"""SQLite cache for ahevents.net FSO event logs."""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from fsostats.parser import EventListing, FSOEvent

DEFAULT_DB_PATH = Path(__file__).parent.parent / "fsostats.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY,
    submitter TEXT,
    event_date TEXT,
    title TEXT,
    frame_title TEXT,
    fetched_at TEXT NOT NULL,
    -- Curated, derived from fsostats.campaigns. `title` stays exactly as
    -- ahevents.net served it; everything user-facing reads these instead.
    display_title TEXT,
    campaign TEXT,
    frame_no INTEGER,
    excluded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS participants (
    event_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    squad TEXT,
    pilot TEXT NOT NULL,
    PRIMARY KEY (event_id, side, pilot)
);

CREATE TABLE IF NOT EXISTS log_entries (
    event_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    pilot TEXT NOT NULL,
    seq INTEGER NOT NULL,
    timestamp TEXT,
    action TEXT NOT NULL,
    plane TEXT,
    other_pilot TEXT,
    detail TEXT,
    raw_text TEXT,
    PRIMARY KEY (event_id, side, pilot, seq)
);

CREATE TABLE IF NOT EXISTS squad_stats (
    event_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    squad TEXT NOT NULL,
    pilots INTEGER, kills INTEGER, assists INTEGER, obj_destroyed INTEGER,
    deaths INTEGER, landed INTEGER, bailed INTEGER, captured INTEGER,
    crashed INTEGER, ditched INTEGER, discos INTEGER,
    PRIMARY KEY (event_id, side, squad)
);

CREATE TABLE IF NOT EXISTS side_stats (
    event_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    pilots INTEGER, kills INTEGER, assists INTEGER, obj_destroyed INTEGER,
    deaths INTEGER, landed INTEGER, bailed INTEGER, captured INTEGER,
    crashed INTEGER, ditched INTEGER, discos INTEGER,
    PRIMARY KEY (event_id, side)
);

CREATE TABLE IF NOT EXISTS top_entries (
    event_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    category TEXT NOT NULL,
    rank INTEGER NOT NULL,
    name TEXT NOT NULL,
    squad TEXT,
    value INTEGER,
    PRIMARY KEY (event_id, side, category, rank)
);

CREATE INDEX IF NOT EXISTS idx_log_entries_pilot ON log_entries(pilot);
CREATE INDEX IF NOT EXISTS idx_log_entries_action ON log_entries(action);
CREATE INDEX IF NOT EXISTS idx_participants_pilot ON participants(pilot);

-- Head-to-head reads log_entries by the *other* party as often as by
-- the pilot, and folds case throughout: 165 handles differ from another
-- only by capitalisation. A plain index can't serve a NOCASE
-- comparison, hence the separate NOCASE pair - without them a single
-- head-to-head lookup takes minutes instead of a fraction of a second.
CREATE INDEX IF NOT EXISTS idx_log_entries_other_pilot ON log_entries(other_pilot);
CREATE INDEX IF NOT EXISTS idx_log_entries_event_action ON log_entries(event_id, action);
CREATE INDEX IF NOT EXISTS idx_log_pilot_nocase ON log_entries(pilot COLLATE NOCASE, action);
CREATE INDEX IF NOT EXISTS idx_log_other_nocase ON log_entries(other_pilot COLLATE NOCASE, action);
CREATE INDEX IF NOT EXISTS idx_participants_pilot_nocase ON participants(pilot COLLATE NOCASE);
"""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class FSODB:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns to a database created before they existed.

        CREATE TABLE IF NOT EXISTS silently does nothing on an existing
        table, so a shipped database would otherwise never gain them.
        """
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(events)")}
        for column, ddl in (
            ("display_title", "TEXT"),
            ("campaign", "TEXT"),
            ("frame_no", "INTEGER"),
            ("excluded", "INTEGER NOT NULL DEFAULT 0"),
            ("one_sided", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in have:
                self._conn.execute(f"ALTER TABLE events ADD COLUMN {column} {ddl}")

    def close(self):
        self._conn.close()

    # -- event list ----------------------------------------------------

    def upsert_event_listings(self, listings: list[EventListing]) -> None:
        now = _now()
        rows = [(l.event_id, l.submitter, l.event_date, l.title, now) for l in listings]
        self._conn.executemany(
            "INSERT INTO events (event_id, submitter, event_date, title, fetched_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(event_id) DO UPDATE SET submitter=excluded.submitter, "
            "event_date=excluded.event_date, title=excluded.title",
            rows,
        )
        self._conn.commit()

    def get_known_event_ids(self) -> set[int]:
        return {r["event_id"] for r in self._conn.execute("SELECT event_id FROM events").fetchall()}

    def get_fetched_event_ids(self) -> set[int]:
        """Events whose full log has already been fetched and stored
        (frame_title is only set once the log itself has been parsed)."""
        return {
            r["event_id"] for r in
            self._conn.execute("SELECT event_id FROM events WHERE frame_title IS NOT NULL").fetchall()
        }

    def get_events(self):
        return self._conn.execute("SELECT * FROM events ORDER BY event_date DESC").fetchall()

    def get_frames(self, include_excluded: bool = False):
        """Frames for display: those with logs, sensibly titled.

        Excludes the 73 events ahevents.net can't render (they have no
        logs and never will - see FINDINGS.md) along with the duplicate
        submissions and test events listed in campaigns.EXCLUDED.
        """
        sql = """
            SELECT event_id, event_date,
                   COALESCE(display_title, title) AS name,
                   campaign, frame_no, one_sided, title AS source_title
            FROM events
            WHERE EXISTS (SELECT 1 FROM log_entries l WHERE l.event_id = events.event_id)
        """
        if not include_excluded:
            sql += " AND excluded = 0"
        return self._conn.execute(sql + " ORDER BY event_date DESC").fetchall()

    def execute(self, sql: str, params=()):
        """Run a read-only query. Lets query modules such as
        fsostats.headtohead take an FSODB or a bare sqlite3 connection
        interchangeably."""
        return self._conn.execute(sql, params)

    def get_pilot_names(self) -> list[str]:
        """Every pilot who appears in a frame that is shown, sorted.

        Backs the pilot type-ahead: FSO handles are easy to mistype, and
        there are over four thousand of them.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT p.pilot FROM participants p "
            "JOIN events e ON e.event_id = p.event_id "
            "WHERE e.excluded = 0 AND p.pilot <> '' "
            "ORDER BY p.pilot COLLATE NOCASE"
        ).fetchall()
        return [r["pilot"] for r in rows]

    def get_frame_summaries(self):
        """One row per displayed frame, with its pilot and sortie counts.

        Counts come from `participants` and `log_entries` rather than
        `side_stats`, so a frame whose posted totals are missing still
        reports what its log actually contains.
        """
        return self._conn.execute("""
            SELECT e.event_id,
                   substr(e.event_date, 1, 10)          AS date,
                   COALESCE(e.display_title, e.title)   AS name,
                   e.campaign,
                   e.frame_no,
                   e.one_sided,
                   (SELECT COUNT(*) FROM participants p
                     WHERE p.event_id = e.event_id)     AS pilots,
                   (SELECT COUNT(*) FROM log_entries l
                     WHERE l.event_id = e.event_id
                       AND l.action = 'sortie_start')    AS sorties,
                   (SELECT COUNT(*) FROM log_entries l
                     WHERE l.event_id = e.event_id
                       AND l.action = 'kill')            AS kills
            FROM events e
            WHERE e.excluded = 0
              AND EXISTS (SELECT 1 FROM log_entries l WHERE l.event_id = e.event_id)
            ORDER BY e.event_date DESC
        """).fetchall()

    def get_campaign_summaries(self):
        """One row per campaign - the unit users think in alongside frames.

        An FSO campaign is normally three frames on consecutive Fridays;
        see fsostats.campaigns for how they're grouped.
        """
        return self._conn.execute("""
            SELECT e.campaign                            AS name,
                   COUNT(*)                              AS frames,
                   MIN(substr(e.event_date, 1, 10))      AS first_date,
                   MAX(substr(e.event_date, 1, 10))      AS last_date,
                   SUM(e.one_sided)                      AS one_sided,
                   (SELECT COUNT(DISTINCT p.pilot) FROM participants p
                     WHERE p.event_id IN (
                       SELECT event_id FROM events x
                        WHERE x.campaign = e.campaign AND x.excluded = 0)) AS pilots,
                   (SELECT COUNT(*) FROM log_entries l
                     WHERE l.action = 'sortie_start' AND l.event_id IN (
                       SELECT event_id FROM events x
                        WHERE x.campaign = e.campaign AND x.excluded = 0)) AS sorties,
                   (SELECT COUNT(*) FROM log_entries l
                     WHERE l.action = 'kill' AND l.event_id IN (
                       SELECT event_id FROM events x
                        WHERE x.campaign = e.campaign AND x.excluded = 0)) AS kills
            FROM events e
            WHERE e.excluded = 0 AND e.campaign IS NOT NULL
              AND EXISTS (SELECT 1 FROM log_entries l WHERE l.event_id = e.event_id)
            GROUP BY e.campaign
            ORDER BY MAX(e.event_date) DESC
        """).fetchall()

    def apply_campaign_metadata(self) -> dict[str, int]:
        """Recompute display_title / campaign / frame_no / excluded for
        every event from fsostats.campaigns.

        Safe and cheap to re-run; it's the step that picks up a
        correction added to campaigns.py, so run it after any scrape.
        """
        from fsostats import campaigns

        import datetime

        rows = self._conn.execute(
            "SELECT event_id, event_date, title FROM events ORDER BY event_date").fetchall()

        # Frames whose log holds only one side. The other team's log was
        # never posted and never turned up later - checked, no frame has
        # a same-date counterpart. Flagged rather than dropped: the side
        # that *was* posted is real data.
        one_sided = {
            r[0] for r in self._conn.execute(
                "SELECT event_id FROM log_entries GROUP BY event_id "
                "HAVING COUNT(DISTINCT side) = 1")
        }

        counts = {"curated": 0, "excluded": 0, "clustered": 0, "one_sided": 0}
        clusterable = []
        resolved: dict[int, tuple[str | None, int | None, bool]] = {}
        for row in rows:
            eid, title = row["event_id"], row["title"] or ""
            campaign, frame_no, is_excluded = campaigns.resolve(eid, title)
            resolved[eid] = (campaign, frame_no, is_excluded)
            if is_excluded:
                counts["excluded"] += 1
                continue
            if campaign:
                counts["curated"] += 1
            clusterable.append({
                "id": eid,
                "date": datetime.date.fromisoformat(row["event_date"][:10]),
                "title": title, "frame_no": frame_no, "campaign": campaign,
            })

        # Only frames with logs are clustered - a frame the site cannot
        # render has nothing to group and would leave phantom gaps.
        with_logs = {r[0] for r in self._conn.execute(
            "SELECT DISTINCT event_id FROM log_entries")}
        clusterable = [f for f in clusterable if f["id"] in with_logs]

        groups = campaigns.cluster(clusterable)

        # Campaign names repeat across the years - Battle of Britain ran
        # in 2012, 2014, 2015 and 2016, Pantelleria three times - so the
        # name alone cannot identify a campaign. Anything grouping by it
        # would silently merge them: Pantelleria came out as one
        # nine-frame campaign numbered 1,1,1,2,2,2,3,3,3. Repeated names
        # get their year, and a second collision its month too.
        names = [campaigns.campaign_name(g) for g in groups]
        repeated = {n for n in names if names.count(n) > 1}
        used: set[str] = set()
        campaign_of: dict[int, str] = {}
        for group, name in zip(groups, names):
            if name in repeated:
                start = min(f["date"] for f in group)
                name = f"{name} ({start.year})"
                if name in used:
                    name = f"{name[:-1]}-{start:%m})"
            used.add(name)
            for frame in group:
                campaign_of[frame["id"]] = name
        counts["clustered"] = len(groups)

        updates = []
        for row in rows:
            eid, title = row["event_id"], row["title"] or ""
            curated, frame_no, is_excluded = resolved[eid]
            campaign = curated or campaign_of.get(eid)
            if campaign and frame_no:
                display = f"{campaign} - Frame {frame_no}"
            elif campaign:
                display = campaign
            else:
                display = title
            is_one_sided = eid in one_sided
            counts["one_sided"] += int(is_one_sided)
            updates.append((display, campaign, frame_no, int(is_excluded),
                            int(is_one_sided), eid))

        with self._conn:
            self._conn.executemany(
                "UPDATE events SET display_title=?, campaign=?, frame_no=?, "
                "excluded=?, one_sided=? WHERE event_id=?",
                updates,
            )
        return counts

    # -- one event's full log ------------------------------------------

    def has_event_log(self, event_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE event_id=? AND frame_title IS NOT NULL", (event_id,)
        ).fetchone()
        return row is not None

    def save_event(self, event: FSOEvent) -> None:
        """Fully replaces one event's stored data. Wrapped in a single
        transaction (Connection used as a context manager: commits on
        success, rolls back on any exception) so a mid-save failure can't
        leave frame_title set with partial/missing child rows - that
        would make has_event_log() wrongly report the event as complete
        and skip it on a resumed scrape."""
        now = _now()
        eid = event.event_id

        with self._conn:
            self._conn.execute(
                "UPDATE events SET frame_title=?, fetched_at=? WHERE event_id=?",
                (event.frame_title, now, eid),
            )
            for table in ("participants", "log_entries", "squad_stats", "side_stats", "top_entries"):
                self._conn.execute(f"DELETE FROM {table} WHERE event_id=?", (eid,))

            self._conn.executemany(
                "INSERT INTO participants (event_id, side, squad, pilot) VALUES (?,?,?,?)",
                [(eid, p.side, p.squad, p.pilot) for p in event.participants],
            )
            self._conn.executemany(
                "INSERT INTO log_entries (event_id, side, pilot, seq, timestamp, action, plane, "
                "other_pilot, detail, raw_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (eid, l.side, l.pilot, l.seq, l.timestamp, l.action, l.plane, l.other_pilot, l.detail, l.raw_text)
                    for l in event.log_entries
                ],
            )
            self._conn.executemany(
                "INSERT INTO squad_stats (event_id, side, squad, pilots, kills, assists, obj_destroyed, "
                "deaths, landed, bailed, captured, crashed, ditched, discos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (eid, s.side, s.squad, s.pilots, s.kills, s.assists, s.obj_destroyed, s.deaths,
                     s.landed, s.bailed, s.captured, s.crashed, s.ditched, s.discos)
                    for s in event.squad_stats
                ],
            )
            self._conn.executemany(
                "INSERT INTO side_stats (event_id, side, pilots, kills, assists, obj_destroyed, "
                "deaths, landed, bailed, captured, crashed, ditched, discos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (eid, s.side, s.pilots, s.kills, s.assists, s.obj_destroyed, s.deaths,
                     s.landed, s.bailed, s.captured, s.crashed, s.ditched, s.discos)
                    for s in event.side_stats
                ],
            )
            self._conn.executemany(
                "INSERT INTO top_entries (event_id, side, category, rank, name, squad, value) VALUES (?,?,?,?,?,?,?)",
                [(eid, t.side, t.category, t.rank, t.name, t.squad, t.value) for t in event.top_entries],
            )

    # -- queries ---------------------------------------------------------

    def get_pilot_career_kills(self, pilot: str):
        """Total kills (kill + ground_kill) for a pilot across every stored event."""
        return self._conn.execute(
            "SELECT action, COUNT(*) as n FROM log_entries WHERE pilot=? COLLATE NOCASE "
            "GROUP BY action",
            (pilot,),
        ).fetchall()

    def get_pilot_events(self, pilot: str):
        return self._conn.execute(
            "SELECT DISTINCT e.event_id, e.event_date, e.title, e.frame_title, p.side, p.squad "
            "FROM participants p JOIN events e ON e.event_id = p.event_id "
            "WHERE p.pilot=? COLLATE NOCASE ORDER BY e.event_date DESC",
            (pilot,),
        ).fetchall()
