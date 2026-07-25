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
    fetched_at TEXT NOT NULL
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
"""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class FSODB:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

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
