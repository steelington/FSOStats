"""Pilot careers, aircraft attribution, head-to-head, squads, boards.

These run against the toy database from conftest, where the right
answer is known by construction.
"""
import pytest

from fsostats import headtohead, leaderboard, pilot, squads
from tests.conftest import add_event, add_log, add_pilot


class TestCareer:
    def test_totals(self, toy):
        career = pilot.career(toy, "Ace")
        assert career["frames"] == 2
        assert career["sorties"] == 3
        assert career["kills"] == 4
        assert career["deaths"] == 1
        assert career["landed"] == 2
        assert career["exploded"] == 1

    def test_kd(self, toy):
        assert pilot.career(toy, "Ace")["kd"] == 4.0

    def test_handles_fold_case(self, toy):
        assert pilot.career(toy, "ACE")["kills"] == pilot.career(toy, "Ace")["kills"]

    def test_unknown_pilot_is_empty_not_an_error(self, toy):
        assert pilot.career(toy, "Nobody")["frames"] == 0

    def test_excluded_events_do_not_count(self, toy):
        add_event(toy, 3, "2020-01-17", "Duplicate Frame 1", excluded=1)
        add_pilot(toy, 3, "Allies", "Test Squad", "Ace")
        add_log(toy, 3, "Allies", "Ace", 1, "sortie_start", plane="Spitfire Mk IX")
        add_log(toy, 3, "Allies", "Ace", 2, "kill", plane="Bf 109G-6", other="Baron")
        toy._conn.commit()
        assert pilot.career(toy, "Ace")["kills"] == 4   # unchanged


class TestAircraftAttribution:
    """The heart of it: a kill row names the VICTIM's aircraft, so kills
    must be attributed to the sortie they fell within."""

    def test_attributes_kills_to_what_the_pilot_flew(self, toy):
        by_plane = {a["plane"]: a for a in pilot.aircraft(toy, "Ace")}
        assert by_plane["Spitfire Mk IX"]["kills"] == 3   # 2 in frame 1, 1 in frame 2
        assert by_plane["Hurricane Mk II"]["kills"] == 1
        # Never the victim's aircraft.
        assert "Bf 109G-6" not in by_plane

    def test_attributes_the_death_to_the_right_sortie(self, toy):
        by_plane = {a["plane"]: a for a in pilot.aircraft(toy, "Ace")}
        assert by_plane["Spitfire Mk IX"]["deaths"] == 1
        assert by_plane["Hurricane Mk II"]["deaths"] == 0

    def test_totals_reconcile_with_the_career(self, toy):
        career = pilot.career(toy, "Ace")
        planes = pilot.aircraft(toy, "Ace")
        assert sum(a["kills"] for a in planes) == career["kills"]
        assert sum(a["sorties"] for a in planes) == career["sorties"]
        assert sum(a["deaths"] for a in planes) == career["deaths"]

    def test_entries_before_any_sortie_are_kept_not_dropped(self, toy):
        """A log starting without a take-off happens in ~4% of frames.
        Those kills can't be pinned to a type, but must still be counted
        or the aircraft table stops summing to the career."""
        add_event(toy, 4, "2020-02-07", "Odd Frame 1")
        add_pilot(toy, 4, "Allies", "Test Squad", "Ace")
        add_log(toy, 4, "Allies", "Ace", 1, "kill", plane="Bf 109G-6", other="Baron")
        toy._conn.commit()
        planes = pilot.aircraft(toy, "Ace")
        assert pilot.UNATTRIBUTED in {a["plane"] for a in planes}
        assert sum(a["kills"] for a in planes) == pilot.career(toy, "Ace")["kills"]


class TestFrames:
    def test_one_row_per_frame_with_results(self, toy):
        frames = pilot.frames(toy, "Ace")
        assert len(frames) == 2
        assert frames[0]["date"] == "2020-01-10"      # newest first
        assert frames[1]["kills"] == 3

    def test_lists_every_aircraft_flown(self, toy):
        first = [f for f in pilot.frames(toy, "Ace") if f["date"] == "2020-01-03"][0]
        assert "Spitfire Mk IX" in first["planes"]
        assert "Hurricane Mk II" in first["planes"]


class TestHeadToHead:
    def test_counts_a_shootdown_once(self, toy):
        """Baron killing Ace is logged twice - Baron's kill row and
        Ace's death row. It must count once."""
        assert dict(headtohead.nemeses(toy, "Ace")) == {"Baron": 1}

    def test_victims(self, toy):
        assert dict(headtohead.victims(toy, "Ace")) == {"Baron": 4}

    def test_opponents_carry_both_directions(self, toy):
        opponent = headtohead.opponents(toy, "Ace")[0]
        assert (opponent.name, opponent.killed, opponent.killed_by) == ("Baron", 4, 1)
        assert opponent.diff == 3

    def test_the_two_pilots_agree(self, toy):
        ace = headtohead.opponents(toy, "Ace")[0]
        baron = headtohead.opponents(toy, "Baron")[0]
        assert (ace.killed, ace.killed_by) == (baron.killed_by, baron.killed)

    def test_shared_frame_count(self, toy):
        assert headtohead.opponents(toy, "Ace")[0].frames == 2

    def test_record_against_matches_the_summary(self, toy):
        record = headtohead.record_against(toy, "Ace", "Baron")
        killed = sum(1 for r in record if r["direction"] == "killed")
        died = sum(1 for r in record if r["direction"] == "killed by")
        opponent = headtohead.opponents(toy, "Ace")[0]
        assert (killed, died) == (opponent.killed, opponent.killed_by)

    def test_death_with_no_matching_kill_is_still_counted(self, toy):
        """A team can log being shot down by someone whose own side
        never logged the kill - 4,410 real cases."""
        add_event(toy, 5, "2020-03-06", "Lonely Frame 1")
        add_pilot(toy, 5, "Allies", "Test Squad", "Ace")
        add_log(toy, 5, "Allies", "Ace", 1, "sortie_start", plane="Spitfire Mk IX")
        add_log(toy, 5, "Allies", "Ace", 2, "death", other="Ghost")
        toy._conn.commit()
        assert dict(headtohead.nemeses(toy, "Ace"))["Ghost"] == 1

    def test_folds_case(self, toy):
        assert headtohead.victims(toy, "ace") == headtohead.victims(toy, "Ace")


class TestSquadKey:
    @pytest.mark.parametrize("a,b", [
        ("325th  Checkertails VFG", "325th Checkertails VFG"),
        ("~~~THE UNFORGIVEN~~~", "~~~The Unforgiven~~~"),
        ("CLAIM JUMPERS", "CLAIMJUMPERS"),
        ("9 GIAP VVS RKKA", "9GIAP VVS RKKA"),
        ('JG54 "Grunherz"', "JG54 Grunherz"),
        ('162ndFG"Purple*Hearts"', '162ndFG\\"Purple*Hearts\\"'),
        ("JG/2", "JG2"),
    ])
    def test_folds_the_same_squad(self, a, b):
        assert squads.squad_key(a) == squads.squad_key(b)

    @pytest.mark.parametrize("a,b", [
        ("II./JG11", "III./JG11 Black Hearts"),
        ("56th FG", "56th FG Zemke's Wolfpack"),
        ("364th C-Hawks", "364th C-Hawks FG"),
        ("Arabian Knights", "327th Steel Talons"),
    ])
    def test_keeps_different_squads_apart(self, a, b):
        assert squads.squad_key(a) != squads.squad_key(b)

    def test_empty_name(self):
        assert squads.squad_key("") == ""


class TestLeaderboard:
    def test_pilot_totals_count_frames_flown_not_frames_scored(self, toy):
        """Ace flew 2 frames and scored in both; add a scoreless frame
        and the frame count must rise while the total does not."""
        before = [r for r in leaderboard.pilot_totals(toy, "kills") if r["pilot"] == "Ace"][0]
        add_event(toy, 6, "2020-04-03", "Quiet Frame 1")
        add_pilot(toy, 6, "Allies", "Test Squad", "Ace")
        add_log(toy, 6, "Allies", "Ace", 1, "sortie_start", plane="Spitfire Mk IX")
        toy._conn.commit()
        after = [r for r in leaderboard.pilot_totals(toy, "kills") if r["pilot"] == "Ace"][0]
        assert after["total"] == before["total"]
        assert after["frames"] == before["frames"] + 1
        assert after["per_frame"] < before["per_frame"]

    def test_ranks_by_total(self, toy):
        board = leaderboard.pilot_totals(toy, "kills")
        assert board[0]["pilot"] == "Ace"
        assert [r["total"] for r in board] == sorted(
            (r["total"] for r in board), reverse=True)
