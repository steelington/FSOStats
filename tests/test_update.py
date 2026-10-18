"""The in-app Update job, with the network replaced by canned responses."""
from __future__ import annotations

from fsostats import update
from fsostats.parser import EventListing, FSOEvent


def _listing(event_id, date, title):
    return EventListing(event_id=event_id, submitter="CM", event_date=f"{date} 20:00:00",
                        title=title)


def _run(monkeypatch, db, listings):
    """Run an Updater to the end and save what it hands back, as the GUI does."""
    class FakeClient:
        def __init__(self, **_):
            pass

        def fetch_event_list(self, event_type="FSO", since=None):
            return {}

        def fetch_event(self, event_id):
            return {"event_id": event_id}

    monkeypatch.setattr(update, "FSOClient", FakeClient)
    monkeypatch.setattr(update, "listings_from_api", lambda data: listings)
    monkeypatch.setattr(update, "event_from_api",
                        lambda data: FSOEvent(event_id=data["event_id"],
                                              frame_title=f"Frame {data['event_id']}"))

    updater = update.Updater(since="2026-07-24", known_event_ids=db.get_fetched_event_ids(),
                             interval=0)
    updater._run()
    while not updater.messages.empty():
        message = updater.messages.get()
        if message.event is not None:
            update.save(db, message)


def test_a_new_frame_gets_its_events_row(monkeypatch, db):
    # A frame the database has never seen has no events row for
    # save_event to UPDATE; without the listing it vanished silently.
    _run(monkeypatch, db, [_listing(4319, "2026-10-16", "When Eagles Dared Frame 3")])

    row = db.execute("SELECT event_date, title, frame_title FROM events "
                     "WHERE event_id = 4319").fetchone()
    assert tuple(row) == ("2026-10-16 20:00:00", "When Eagles Dared Frame 3", "Frame 4319")
    assert 4319 in db.get_fetched_event_ids()


def test_frames_on_or_before_the_cutoff_are_left_alone(monkeypatch, db):
    _run(monkeypatch, db, [_listing(4295, "2026-07-24", "August Storm Frame 3")])

    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
