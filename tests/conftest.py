"""Shared fixtures.

Most tests run against a small hand-built database rather than the real
one, so they assert on data whose right answer is known by construction.
A few integration tests do use the real database - those skip when it
isn't present, so the suite still runs on a fresh checkout.
"""
from __future__ import annotations

import datetime
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fsostats.db import FSODB  # noqa: E402  (after sys.path)

REAL_DB = ROOT / "fsostats.db"


@pytest.fixture
def db(tmp_path):
    """An empty FSODB on disk, with the full schema."""
    database = FSODB(tmp_path / "test.db")
    yield database
    database.close()


def add_event(db, event_id, date, title, excluded=0):
    db.execute(
        "INSERT INTO events (event_id, event_date, title, frame_title, fetched_at, "
        "excluded, one_sided) VALUES (?,?,?,?,?,?,0)",
        (event_id, f"{date} 20:00:00", title, title, "now", excluded))


def add_pilot(db, event_id, side, squad, pilot):
    db.execute("INSERT INTO participants (event_id, side, squad, pilot) VALUES (?,?,?,?)",
               (event_id, side, squad, pilot))


def add_log(db, event_id, side, pilot, seq, action, plane=None, other=None, detail=None):
    db.execute(
        "INSERT INTO log_entries (event_id, side, pilot, seq, action, plane, "
        "other_pilot, detail, raw_text) VALUES (?,?,?,?,?,?,?,?,?)",
        (event_id, side, pilot, seq, action, plane, other, detail, ""))


@pytest.fixture
def toy(db):
    """A two-frame world with known answers.

    Frame 1 (event 1): Ace flies a Spitfire, kills Baron twice, dies once
    to Baron. Then switches to a Hurricane and kills Baron again.
    Frame 2 (event 2): Ace flies a Spitfire, one kill, no deaths.

    So Ace totals 2 frames, 3 sorties, 4 kills and 1 death, splitting
    per aircraft as Spitfire 3k/1d over 2 sorties and Hurricane 1k/0d
    over 1. Baron is shot down 4 times and shoots Ace down once - and
    that one shootdown is written twice, as Baron's kill row and Ace's
    death row, which is the double-count head-to-head has to avoid.
    """
    add_event(db, 1, "2020-01-03", "Toy Campaign Frame 1")
    add_event(db, 2, "2020-01-10", "Toy Campaign Frame 2")
    for event_id in (1, 2):
        add_pilot(db, event_id, "Allies", "Test Squad", "Ace")
        add_pilot(db, event_id, "Axis", "Enemy Squad", "Baron")

    # Frame 1, Ace's log in order.
    add_log(db, 1, "Allies", "Ace", 1, "sortie_start", plane="Spitfire Mk IX")
    add_log(db, 1, "Allies", "Ace", 2, "kill", plane="Bf 109G-6", other="Baron")
    add_log(db, 1, "Allies", "Ace", 3, "kill", plane="Bf 109G-6", other="Baron")
    add_log(db, 1, "Allies", "Ace", 4, "death", other="Baron", detail="exploded")
    add_log(db, 1, "Allies", "Ace", 5, "sortie_start", plane="Hurricane Mk II")
    add_log(db, 1, "Allies", "Ace", 6, "kill", plane="Bf 109G-6", other="Baron")
    add_log(db, 1, "Allies", "Ace", 7, "landed")
    # Baron's side of the same frame: his kill on Ace is already a kill
    # row above from Ace's perspective? No - Baron killed Ace, so Baron
    # has the kill row and Ace has the death row. Both describe one
    # shootdown, which is exactly the double-count the code must avoid.
    add_log(db, 1, "Axis", "Baron", 1, "sortie_start", plane="Bf 109G-6")
    add_log(db, 1, "Axis", "Baron", 2, "kill", plane="Spitfire Mk IX", other="Ace")
    add_log(db, 1, "Axis", "Baron", 3, "death", other="Ace")

    # Frame 2.
    add_log(db, 2, "Allies", "Ace", 1, "sortie_start", plane="Spitfire Mk IX")
    add_log(db, 2, "Allies", "Ace", 2, "kill", plane="Bf 109G-6", other="Baron")
    add_log(db, 2, "Allies", "Ace", 3, "landed")
    add_log(db, 2, "Axis", "Baron", 1, "sortie_start", plane="Bf 109G-6")
    add_log(db, 2, "Axis", "Baron", 2, "death", other="Ace")

    db._conn.commit()
    return db


@pytest.fixture
def real_db():
    """The live database, skipped when absent."""
    if not REAL_DB.exists():
        pytest.skip("fsostats.db not present")
    conn = sqlite3.connect(REAL_DB)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
