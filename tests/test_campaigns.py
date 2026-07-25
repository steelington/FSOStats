"""Frame numbering, title normalisation, and campaign clustering."""
import datetime

import pytest

from fsostats import campaigns


class TestFrameNumber:
    @pytest.mark.parametrize("title,expected", [
        ("August Storm Frame 3", 3),
        ("Operation Torch Algiers:1942 Fr 2", 2),
        ("OUTOFTHESUNFrame3", 3),                    # no separator
        ("Frame 1:  Operation Bodenplatte", 1),      # leading
        ("Devils Broom Stick Fr3", 3),
        ("Leaning Into France 1941 Frame One", 1),   # word numbers
        ("Citadel on the Danube Frame Two", 2),
        ("Just Another Day 1943 Frame Three", 3),
        ("Operation Panzerfaust Frame I", 1),        # roman
        ("Operation Iceberg - Frame II", 2),
        ("Combat Challenge Frame III", 3),
        ("KanalKampfe to Adlertag; Frame 3.1", 3),   # split-by-side
        ("Battle of Britain", None),                 # none stated
        ("Normandy 1944", None),                     # a year is not a frame
    ])
    def test_reads_every_spelling(self, title, expected):
        assert campaigns.frame_number_from_title(title) == expected

    def test_rejects_out_of_range(self):
        # A year or a base number must never be read as a frame.
        assert campaigns.frame_number_from_title("Frame 1944") is None


class TestCampaignKey:
    def test_ignores_case_spacing_and_punctuation(self):
        assert (campaigns.campaign_key("Operation Clarion - 1945 - Frame 2")
                == campaigns.campaign_key("OPERATION CLARION 1945"))

    def test_strips_month_prefix(self):
        assert (campaigns.campaign_key("July 2019 FSO: Stalemate At Anzio")
                == campaigns.campaign_key("Stalemate at Anzio"))

    def test_strips_trailing_logs(self):
        assert (campaigns.campaign_key("The Luftwaffe end is near Frame 2 logs")
                == campaigns.campaign_key("The Luftwaffe End is Near"))


class TestSimilarity:
    def _score(self, a, b):
        return campaigns.title_similarity(
            campaigns.campaign_tokens(a), campaigns.campaign_key(a),
            campaigns.campaign_tokens(b), campaigns.campaign_key(b))

    def test_survives_word_order_reversal(self):
        # The case sequence matching alone cannot handle.
        score = self._score("Tunisia: Verdun of the Mediterranean 1943",
                            "1943: Tunisia - The Verdun of Mediterranean")
        assert score >= campaigns.SIMILARITY

    def test_survives_spelling_drift(self):
        assert self._score("Germanys Last Stand - North Sea 1945",
                           "Germanys last stand-North Sea 1945") >= campaigns.SIMILARITY

    def test_separates_unrelated_campaigns(self):
        assert self._score("August Storm", "Operation Torch: Algiers 1942") \
            < campaigns.SIMILARITY


def _frames(*specs):
    """(day, title, frame_no) triples -> the dicts cluster() expects."""
    return [
        {"id": i, "date": datetime.date.fromisoformat(day), "title": title,
         "frame_no": no, "campaign": None}
        for i, (day, title, no) in enumerate(specs, 1)
    ]


class TestCluster:
    def test_groups_three_weekly_frames(self):
        groups = campaigns.cluster(_frames(
            ("2026-07-10", "August Storm Frame 1", 1),
            ("2026-07-17", "August Storm Frame 2", 2),
            ("2026-07-24", "August Storm Frame 3", 3)))
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_keeps_reruns_years_apart_separate(self):
        groups = campaigns.cluster(_frames(
            ("2012-05-04", "Battle Of Britain Frame 1", 1),
            ("2012-05-11", "Battle Of Britain Frame 2", 2),
            ("2014-08-08", "Battle of Britain Frame 1", 1),
            ("2014-08-15", "Battle of Britain Frame 2", 2)))
        assert len(groups) == 2

    def test_frame_number_clash_starts_a_new_campaign(self):
        """Two identically-named campaigns back to back must not merge
        just because the gap is short - the repeated number gives it
        away. This is the constraint that makes the wider date window
        safe."""
        groups = campaigns.cluster(_frames(
            ("2020-01-03", "Some Campaign Frame 1", 1),
            ("2020-01-10", "Some Campaign Frame 2", 2),
            ("2020-01-17", "Some Campaign Frame 1", 1)))
        assert len(groups) == 2
        assert len(groups[0]) == 2 and len(groups[1]) == 1

    def test_same_date_duplicate_number_is_one_frame_split_by_side(self):
        """3540/3541 - 'Frame 3.1' and 'Frame 3.2', one side each."""
        groups = campaigns.cluster(_frames(
            ("2019-08-09", "Kanalkampf to Adlertag Frame 1", 1),
            ("2019-08-23", "KanalKampfe to Adlertag Frame 3.1", 3),
            ("2019-08-23", "KanalKampfe to Adlertag Frame 3.2", 3)))
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_gap_beyond_the_window_splits(self):
        groups = campaigns.cluster(_frames(
            ("2020-01-03", "Long Gap Frame 1", 1),
            ("2020-03-06", "Long Gap Frame 2", 2)))
        assert len(groups) == 2

    def test_spans_a_missing_middle_frame(self):
        """Crimean Catastrophe: frame 2's log was never rendered, so
        frames 1 and 3 sit 14 days apart and must still group."""
        groups = campaigns.cluster(_frames(
            ("2016-09-09", "Crimean Catastrophe Frame 1", 1),
            ("2016-09-23", "Crimean Catastrophe Frame 3", 3)))
        assert len(groups) == 1

    def test_curated_name_beats_title_similarity(self):
        """Nothing about the titles connects these - the campaign was
        renamed after frame 1."""
        frames = _frames(
            ("2025-01-10", "Thunder over the Solomons", 1),
            ("2025-01-17", "Bougainville 43", 2),
            ("2025-01-24", "Bougainville 43", 3))
        for frame in frames:
            frame["campaign"] = "Thunder over the Solomons"
        groups = campaigns.cluster(frames)
        assert len(groups) == 1
        assert len(groups[0]) == 3


class TestCampaignName:
    def test_prefers_the_fullest_title(self):
        frames = [{"title": "Clarion - 1945 - Frame 2", "campaign": None},
                  {"title": "Operation Clarion 1945 - Frame 1", "campaign": None}]
        assert campaigns.campaign_name(frames) == "Operation Clarion 1945"

    def test_strips_leading_punctuation_left_by_the_marker(self):
        frames = [{"title": "Frame 1:  Operation Bodenplatte", "campaign": None}]
        assert campaigns.campaign_name(frames) == "Operation Bodenplatte"

    def test_curated_name_wins(self):
        frames = [{"title": "Bougainville 43", "campaign": "Thunder over the Solomons"}]
        assert campaigns.campaign_name(frames) == "Thunder over the Solomons"


class TestCuratedData:
    """The 53 hand corrections are load-bearing and easy to break."""

    def test_no_event_is_both_overridden_and_excluded(self):
        assert not set(campaigns.OVERRIDES) & set(campaigns.EXCLUDED)

    def test_frame_numbers_are_plausible(self):
        for event_id, (_name, number, _note) in campaigns.OVERRIDES.items():
            assert number is None or 1 <= number <= campaigns.MAX_FRAME_NO, event_id

    def test_curated_campaigns_number_from_one_without_repeats(self):
        by_campaign = {}
        for _eid, (name, number, _note) in campaigns.OVERRIDES.items():
            if number is not None:
                by_campaign.setdefault(name, []).append(number)
        for name, numbers in by_campaign.items():
            assert len(numbers) == len(set(numbers)), f"{name} repeats a frame number"

    def test_resolve_reports_exclusions(self):
        excluded_id = next(iter(campaigns.EXCLUDED))
        assert campaigns.resolve(excluded_id, "anything")[2] is True

    def test_resolve_falls_back_to_the_title(self):
        assert campaigns.resolve(-1, "Something Frame 2") == (None, 2, False)
