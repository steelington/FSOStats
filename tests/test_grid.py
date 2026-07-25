"""Grid helpers: sorting, filtering, CSV export."""
import csv

import pytest

from fsostats.grid import ColumnFilter, as_number, sort_key


class TestSortKey:
    def test_numbers_sort_numerically(self):
        assert sorted(["10", "9", "100"], key=sort_key) == ["9", "10", "100"]

    def test_thousands_separators_and_percentages(self):
        assert sort_key("1,364")[1] == 1364.0
        assert sort_key("42%")[1] == 42.0

    def test_text_sorts_case_insensitively(self):
        assert sorted(["beta", "Alpha"], key=sort_key) == ["Alpha", "beta"]

    def test_blanks_sort_last(self):
        assert sorted(["", "5"], key=sort_key) == ["5", ""]

    def test_mixed_column_does_not_raise(self):
        sorted(["7", "", "text", "1,000"], key=sort_key)


class TestAsNumber:
    @pytest.mark.parametrize("value,expected", [
        ("5", 5.0), ("1,364", 1364.0), ("3.73", 3.73), ("42%", 42.0),
        ("", None), ("N/A", None), (None, None),
    ])
    def test_parses(self, value, expected):
        assert as_number(value) == expected


class TestColumnFilter:
    def test_inactive_by_default(self):
        assert not ColumnFilter().active()

    def test_text_match_ignores_case(self):
        assert ColumnFilter(text="ak").matches("AKWarHwk")
        assert not ColumnFilter(text="zz").matches("AKWarHwk")

    def test_numeric_range(self):
        flt = ColumnFilter(low=10, high=20)
        assert flt.matches("15")
        assert not flt.matches("5")
        assert not flt.matches("25")

    def test_non_numeric_fails_a_range(self):
        assert not ColumnFilter(low=1).matches("text")

    def test_text_and_range_together(self):
        flt = ColumnFilter(text="1", low=100)
        assert flt.matches("150")
        # Matches the text but isn't a number, so the range rejects it.
        assert not flt.matches("150x")
        # A number, but below the floor.
        assert not flt.matches("15")


@pytest.fixture
def grid():
    """A GridView needs a Tk root; skip where there's no display."""
    tk = pytest.importorskip("tkinter")
    ctk = pytest.importorskip("customtkinter")
    from fsostats.grid import GridView
    try:
        root = ctk.CTk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    view = GridView(root, columns=("Name", "Kills"), title="test")
    view.set_rows([("AKWarHwk", 1364), ("Dantoo", 1011), ("Steely", 668)])
    yield view
    root.destroy()


class TestGridView:
    def test_rows_land(self, grid):
        assert len(grid.visible_rows()) == 3

    def test_quick_filter(self, grid):
        grid.set_quick_filter("steel")
        assert [r[0] for r in grid.visible_rows()] == ["Steely"]

    def test_clearing_restores_everything(self, grid):
        grid.set_quick_filter("steel")
        grid.clear_filters()
        assert len(grid.visible_rows()) == 3

    def test_sort_toggles(self, grid):
        grid.sort_by("Kills")
        assert [r[1] for r in grid.visible_rows()] == [668, 1011, 1364]
        grid.sort_by("Kills")
        assert [r[1] for r in grid.visible_rows()] == [1364, 1011, 668]

    def test_export_writes_visible_rows_only(self, grid, tmp_path):
        grid.set_quick_filter("a")            # AKWarHwk, Dantoo
        path = tmp_path / "out.csv"
        grid.export_csv(str(path))
        with open(path, encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == ["Name", "Kills"]
        assert len(rows) == 3                 # header + 2
        assert "Steely" not in [r[0] for r in rows]

    def test_export_follows_the_sort(self, grid, tmp_path):
        grid.sort_by("Kills")
        path = tmp_path / "sorted.csv"
        grid.export_csv(str(path))
        with open(path, encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))[1:]
        assert [r[0] for r in rows] == ["Steely", "Dantoo", "AKWarHwk"]

    def test_column_help_is_offered(self, grid):
        grid.set_column_help({"Kills": "air kills"})
        assert grid._column_help["Kills"] == "air kills"
