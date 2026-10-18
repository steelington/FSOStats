"""Reading aceshigh.events API responses into FSOSTATS's records."""
from __future__ import annotations

import pytest

from fsostats.parser import _classify_log_line, event_from_api, listings_from_api


def _line(side, pilot, seq, text, time="20:00:00"):
    return {"side": side, "side_slot": 0, "pilot": pilot, "seq": seq, "time": time,
            "action": "", "data1": "", "data2": "", "text": text}


def _frame(participants, log, side_stats=(), top=()):
    return {
        "api": 1, "timezone": "America/Chicago",
        "event": {"source_id": 900011, "event_type": "FSO", "title": "When Eagles Dared, Frame 3",
                  "display_title": "When Eagles Dared, Frame 3",
                  "event_date": "2026-10-16 20:25:00", "submitter": "cm", "campaign": "",
                  "frame_no": None, "one_sided": False, "countries": ["Allies", "Axis", "Unused"]},
        "participants": [{"side": s, "side_slot": 0, "squad": q, "pilot": p}
                         for s, q, p in participants],
        "log": list(log),
        "side_stats": list(side_stats),
        "top": list(top),
    }


def test_listings_carry_id_date_and_submitter():
    data = {"api": 1, "events": [
        {"source_id": 900011, "event_type": "FSO", "title": "When Eagles Dared, Frame 3",
         "display_title": "x", "event_date": "2026-10-16 20:25:00", "submitter": "cm_bob",
         "campaign": "", "frame_no": None, "one_sided": False},
    ]}
    [listing] = listings_from_api(data)
    assert (listing.event_id, listing.event_date, listing.submitter, listing.title) == (
        900011, "2026-10-16 20:25:00", "cm_bob", "When Eagles Dared, Frame 3")


def test_an_unknown_api_version_is_refused_not_misread():
    with pytest.raises(ValueError, match="version"):
        listings_from_api({"api": 2, "events": []})


def test_lines_classify_from_their_sentence_and_seq_restarts_per_pilot():
    event = event_from_api(_frame(
        [("Allies", "332nd", "Steely"), ("Axis", "JG2", "Fritz")],
        [_line("Allies", "Steely", 4, "Departed from Field #12 in a P-51D"),
         _line("Allies", "Steely", 9, "Shot down a Fw190A-8 flown by Fritz."),
         _line("Axis", "Fritz", 1, "Departed from Field #30 in a Fw190A-8"),
         _line("Axis", "Fritz", 2, "Was shot down by Steely.")]))

    rows = [(e.pilot, e.seq, e.action, e.plane, e.other_pilot) for e in event.log_entries]
    assert rows == [
        ("Steely", 1, "sortie_start", "P-51D", None),
        ("Steely", 2, "kill", "Fw190A-8", "Fritz"),
        ("Fritz", 1, "sortie_start", "Fw190A-8", None),
        ("Fritz", 2, "death", None, "Steely"),
    ]
    assert event.frame_title == "When Eagles Dared, Frame 3"


def test_the_frame_title_is_the_posted_one_not_the_cleaned_one():
    data = _frame([("Allies", "332nd", "Steely")],
                  [_line("Allies", "Steely", 1, "Skillfully ditched.")])
    data["event"].update(title="August Storm Frame 3", display_title="August Storm - Frame 3")
    assert event_from_api(data).frame_title == "August Storm Frame 3"


def test_one_spelling_per_pilot_so_the_tables_join():
    # FSODB joins participants to log_entries on the exact name, so a log
    # row spelled differently from the roster must take the roster's.
    event = event_from_api(_frame(
        [("Allies", "332nd", "Steely")],
        [_line("Allies", "STEELY", 1, "Departed from Field #12 in a P-51D")]))
    assert [p.pilot for p in event.participants] == ["Steely"]
    assert event.log_entries[0].pilot == "Steely"


def test_a_pilot_missing_from_the_roster_still_counts():
    event = event_from_api(_frame([], [_line("Axis", "Fritz", 1, "Skillfully ditched.")]))
    assert [(p.side, p.squad, p.pilot) for p in event.participants] == [("Axis", "", "Fritz")]


def test_an_empty_frame_is_none_so_it_stays_retryable():
    assert event_from_api(_frame([], [])) is None


@pytest.mark.parametrize("old, new, detail", [
    ("Was shot down by   .", "Was shot down.", None),
    ("Was shot down by   (exploded).", "Was shot down (exploded).", "exploded"),
    ("Was shot down by   (crashed).", "Crashed.", "crashed"),
    ("Was shot down by   and captured.", "Was shot down and captured.", "captured"),
])
def test_an_uncredited_death_reads_the_same_in_both_sites_wording(old, new, detail):
    # ahevents.net printed the template with a gap; aceshigh.events says
    # what it means. Both must be a death with nobody credited.
    expected = {"action": "death", "other_pilot": None, "detail": detail}
    assert _classify_log_line(old) == expected
    assert _classify_log_line(new) == expected


def test_a_credited_death_is_unchanged():
    assert _classify_log_line("Was shot down by Fritz (exploded) and captured.") == {
        "action": "death", "other_pilot": "Fritz", "detail": "exploded, captured"}


def test_an_object_kill_counts_whichever_article_it_has():
    assert _classify_log_line("Destroyed a ammo bunker at base #92")["action"] == "ground_kill"
    assert _classify_log_line("Destroyed an ammo bunker at base #92") == {
        "action": "ground_kill", "detail": "an ammo bunker at base #92"}


def test_missing_totals_are_derived_by_the_old_sites_rules():
    event = event_from_api(_frame(
        [("Allies", "332nd", "Steely"), ("Allies", "332nd", "Wing"), ("Axis", "JG2", "Fritz")],
        [_line("Allies", "Steely", 1, "Shot down a Fw190A-8 flown by Fritz."),
         _line("Allies", "Steely", 2, "Destroyed a field gun at base #12"),
         _line("Allies", "Steely", 3, "Destroyed a city building at city #02"),
         _line("Allies", "Wing", 1, "Was shot down by Fritz and captured."),
         _line("Allies", "Wing", 2, "Vanished without a trace."),
         _line("Axis", "Fritz", 1, "Crashed."),
         _line("Axis", "Fritz", 2, "Arrived Safely at Field #30")]))

    squads = {(s.side, s.squad): s for s in event.squad_stats}
    allied = squads[("Allies", "332nd")]
    # A capture is `captured`, not a death; a city target is an object.
    assert (allied.pilots, allied.kills, allied.obj_destroyed, allied.deaths,
            allied.captured, allied.discos) == (2, 1, 2, 0, 1, 1)
    axis = squads[("Axis", "JG2")]
    # The old site never filled in `crashed`, so a crash is just a death.
    assert (axis.deaths, axis.crashed, axis.landed) == (1, 0, 1)

    sides = {s.side: s for s in event.side_stats}
    assert (sides["Allies"].pilots, sides["Allies"].kills, sides["Allies"].squad) == (2, 1, None)

    tops = {(t.side, t.category): (t.rank, t.name, t.squad, t.value) for t in event.top_entries}
    assert tops[("Allies", "pilot_kills")] == (1, "Steely", "332nd", 1)
    assert tops[("Allies", "squad_obj")] == (1, "332nd", None, 2)
    # Zero-value boards are left out, as the site leaves them out.
    assert ("Axis", "pilot_kills") not in tops


def test_posted_totals_win_over_derived_ones():
    posted = {"side": "Allies", "pilots": 30, "kills": 71, "assists": 26, "obj_destroyed": 126,
              "deaths": 71, "landed": 45, "bailed": 4, "captured": 29, "crashed": 0,
              "ditched": 3, "discos": 0}
    top = {"side": "Allies", "category": "squad_kills", "rank": 1, "name": "332nd",
           "squad": "", "value": 71}
    event = event_from_api(_frame(
        [("Allies", "332nd", "Steely")],
        [_line("Allies", "Steely", 1, "Shot down a Fw190A-8 flown by Fritz.")],
        side_stats=[posted], top=[top]))

    assert (event.side_stats[0].kills, event.side_stats[0].pilots) == (71, 30)
    assert [(t.category, t.name, t.squad, t.value) for t in event.top_entries] == [
        ("squad_kills", "332nd", None, 71)]
    # Squad totals are never posted, so they are always derived.
    assert event.squad_stats[0].kills == 1
