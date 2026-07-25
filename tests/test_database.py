"""Invariants on the real database.

These are the checks that caught the actual defects while this was
being built - duplicate campaigns merged under one name, an aircraft
table 31 kills short, squad totals split across spellings. They belong
in the suite so a later change can't quietly undo them.

Skipped when fsostats.db isn't present.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsostats import campaigns, headtohead, leaderboard, pilot, squads


def _busiest_pilot(real_db):
    return real_db.execute("""
        SELECT p.pilot FROM participants p
        JOIN events e ON e.event_id = p.event_id AND e.excluded = 0
        GROUP BY p.pilot COLLATE NOCASE ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()[0]


class TestCurationApplied:
    def test_every_logged_frame_has_a_name(self, real_db):
        missing = real_db.execute("""
            SELECT COUNT(*) FROM events e WHERE e.excluded = 0
              AND (e.display_title IS NULL OR e.display_title = '')
              AND EXISTS (SELECT 1 FROM log_entries l WHERE l.event_id = e.event_id)
            """).fetchone()[0]
        assert missing == 0

    def test_every_logged_frame_is_in_a_campaign(self, real_db):
        orphans = real_db.execute("""
            SELECT COUNT(*) FROM events e WHERE e.excluded = 0 AND e.campaign IS NULL
              AND EXISTS (SELECT 1 FROM log_entries l WHERE l.event_id = e.event_id)
            """).fetchone()[0]
        assert orphans == 0

    def test_excluded_events_are_the_curated_ones(self, real_db):
        excluded = {r[0] for r in real_db.execute(
            "SELECT event_id FROM events WHERE excluded = 1")}
        assert excluded == set(campaigns.EXCLUDED)

    def test_curated_overrides_all_exist(self, real_db):
        known = {r[0] for r in real_db.execute("SELECT event_id FROM events")}
        assert set(campaigns.OVERRIDES) <= known
        assert set(campaigns.EXCLUDED) <= known


class TestCampaignIntegrity:
    def test_no_campaign_repeats_a_frame_number(self, real_db):
        """A repeat means two campaigns were merged. Two frames sharing
        a date are one frame split by side and are allowed."""
        offenders = []
        for (name,) in real_db.execute(
                "SELECT DISTINCT campaign FROM events "
                "WHERE campaign IS NOT NULL AND excluded = 0"):
            rows = real_db.execute(
                "SELECT frame_no, substr(event_date,1,10) FROM events "
                "WHERE campaign = ? AND excluded = 0 AND frame_no IS NOT NULL",
                (name,)).fetchall()
            numbers = [r[0] for r in rows]
            dates = [r[1] for r in rows]
            if len(numbers) != len(set(numbers)) and len(dates) == len(set(dates)):
                offenders.append(name)
        assert offenders == []

    def test_campaigns_are_mostly_three_frames(self, real_db):
        sizes = [r[0] for r in real_db.execute("""
            SELECT COUNT(*) FROM events WHERE excluded = 0 AND campaign IS NOT NULL
              AND EXISTS (SELECT 1 FROM log_entries l WHERE l.event_id = events.event_id)
            GROUP BY campaign""")]
        assert sum(1 for s in sizes if s == 3) / len(sizes) > 0.85

    def test_a_campaign_never_spans_more_than_a_few_weeks(self, real_db):
        long_ones = real_db.execute("""
            SELECT campaign, julianday(MAX(event_date)) - julianday(MIN(event_date)) AS span
            FROM events WHERE excluded = 0 AND campaign IS NOT NULL
            GROUP BY campaign HAVING span > 45""").fetchall()
        assert [r[0] for r in long_ones] == []


class TestTotalsReconcile:
    def test_aircraft_sum_to_the_career(self, real_db):
        who = _busiest_pilot(real_db)
        career = pilot.career(real_db, who)
        planes = pilot.aircraft(real_db, who)
        assert sum(a["kills"] for a in planes) == career["kills"]
        assert sum(a["sorties"] for a in planes) == career["sorties"]

    def test_frames_sum_to_the_career(self, real_db):
        who = _busiest_pilot(real_db)
        career = pilot.career(real_db, who)
        frames = pilot.frames(real_db, who)
        assert len(frames) == career["frames"]
        assert sum(f["kills"] for f in frames) == career["kills"]

    def test_leaderboard_agrees_with_the_career(self, real_db):
        top = leaderboard.pilot_totals(real_db, "kills", limit=1)[0]
        assert pilot.career(real_db, top["pilot"])["kills"] == top["total"]

    def test_head_to_head_is_at_least_the_career_kills(self, real_db):
        """Head-to-head adds engagements sourced from death rows that no
        kill row covers, so it can exceed the career count but never
        fall short of it."""
        who = _busiest_pilot(real_db)
        career = pilot.career(real_db, who)
        summary = headtohead.summary(real_db, who)
        assert summary["kills"] >= career["kills"]

    def test_record_against_matches_the_opponent_row(self, real_db):
        who = _busiest_pilot(real_db)
        opponent = headtohead.opponents(real_db, who, limit=1)[0]
        record = headtohead.record_against(real_db, who, opponent.name)
        killed = sum(1 for r in record if r["direction"] == "killed")
        died = sum(1 for r in record if r["direction"] == "killed by")
        assert (killed, died) == (opponent.killed, opponent.killed_by)


class TestSquads:
    def test_the_placeholder_is_not_listed_as_a_squad(self, real_db):
        names = {s["name"].casefold() for s in squads.all_squads(real_db)}
        assert "zzz_no_squad" not in names

    def test_frames_never_exceed_the_number_of_frames_that_exist(self, real_db):
        total = real_db.execute("""
            SELECT COUNT(*) FROM events e WHERE excluded = 0
              AND EXISTS (SELECT 1 FROM log_entries l WHERE l.event_id = e.event_id)
            """).fetchone()[0]
        for squad in squads.all_squads(real_db):
            assert squad["frames"] <= total, squad["name"]

    def test_spellings_resolve_back(self, real_db):
        squad = squads.all_squads(real_db)[0]
        spellings = squads.spellings_for(real_db, squad["key"])
        assert spellings
        assert all(squads.squad_key(s) == squad["key"] for s in spellings)


class TestSideRoles:
    def test_every_flying_team_is_classified(self, real_db):
        unclassified = real_db.execute("""
            SELECT COUNT(*) FROM (
              SELECT DISTINCT event_id, side FROM log_entries WHERE action='sortie_start')
            WHERE NOT EXISTS (
              SELECT 1 FROM side_roles r
               WHERE r.event_id = event_id AND r.side = side)""").fetchone()[0]
        assert unclassified == 0

    def test_roles_are_from_the_known_set(self, real_db):
        roles = {r[0] for r in real_db.execute("SELECT DISTINCT role FROM side_roles")}
        assert roles <= {"allied", "axis", "unknown"}

    def test_the_two_teams_of_a_frame_usually_differ(self, real_db):
        """Both teams classified the same way means the inference failed
        for that frame. A little of it is expected; a lot would mean the
        aircraft affinities have broken."""
        same = real_db.execute("""
            SELECT COUNT(*) FROM (
              SELECT event_id FROM side_roles WHERE role <> 'unknown'
              GROUP BY event_id HAVING COUNT(DISTINCT side) = 2
                 AND COUNT(DISTINCT role) = 1)""").fetchone()[0]
        frames = real_db.execute(
            "SELECT COUNT(DISTINCT event_id) FROM side_roles").fetchone()[0]
        assert same / frames < 0.05

    def test_agrees_with_trustworthy_labels_almost_always(self, real_db):
        """Where a frame used the plain Allies/Axis labels, the inferred
        role should match. The residue is the genuinely mislabelled
        frames, which is the point of having this at all."""
        rows = real_db.execute("""
            SELECT side, role FROM side_roles
            WHERE side IN ('Allies','Axis') AND role <> 'unknown'""").fetchall()
        agree = sum(1 for side, role in rows
                    if (side == "Allies") == (role == "allied"))
        assert agree / len(rows) > 0.97
