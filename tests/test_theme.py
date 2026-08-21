"""The light/dark palette, the theme file, and the settings store.

Window-free on purpose: everything here is about the values, not the
widgets. The live switch (ttk restyling, grid re-tagging, chart repaint)
is exercised by AHSTATS's tests/test_theme.py, which shares this design.
"""
import json
import tempfile
from pathlib import Path

import customtkinter as ctk
import pytest

from fsostats import settings, theme

PALETTE = ("BG_DARK", "PANEL_BG", "PANEL_BG_ALT", "BORDER_GRAY", "TEXT_BODY",
           "TEXT_HEADING", "TEXT_MUTED", "TEXT_CREAM", "ACCENT_OLIVE",
           "ACCENT_OLIVE_HOVER", "ACCENT_GREEN", "ACCENT_GREEN_HOVER",
           "ACCENT_BLUE", "ACCENT_RED", "ACCENT_AMBER", "SELECT_FG")


class TestColourPairs:
    """Every palette entry has to be a (light, dark) pair, or the light
    theme is silently half-applied: CTk takes a bare string happily and
    just shows the same colour in both modes."""

    @pytest.mark.parametrize("name", PALETTE)
    def test_is_a_pair_of_hex_colours(self, name):
        value = getattr(theme, name)
        assert isinstance(value, tuple) and len(value) == 2
        for half in value:
            assert len(half) == 7 and half.startswith("#")

    @pytest.mark.parametrize("name", [n for n in PALETTE if not n.startswith("ACCENT_OLIVE")])
    def test_the_two_halves_differ(self, name):
        # Olive is deliberately identical in both - it is the masthead
        # identity - so it is excluded here.
        light, dark = getattr(theme, name)
        assert light != dark

    def test_color_picks_the_half_matching_the_mode(self):
        original = theme.get_mode()
        try:
            ctk.set_appearance_mode("Light")
            assert theme.color(theme.PANEL_BG) == theme.PANEL_BG[0]
            ctk.set_appearance_mode("Dark")
            assert theme.color(theme.PANEL_BG) == theme.PANEL_BG[1]
        finally:
            ctk.set_appearance_mode(original)

    def test_a_flat_string_passes_through(self):
        # Call sites shouldn't have to know which kind they hold.
        assert theme.color("#123456") == "#123456"


class TestThemeJson:
    def test_every_widget_colour_carries_both_modes(self):
        data = json.loads(Path(theme.THEME_JSON_PATH).read_text(encoding="utf-8"))
        for widget, options in data.items():
            if widget == "CTkFont":
                continue
            for option, value in options.items():
                if not option.endswith("color") or value == "transparent":
                    continue
                assert isinstance(value, list) and len(value) == 2, f"{widget}.{option}"


class TestSettings:
    """A preference is never worth taking the app down for, so every
    failure path has to land on the defaults instead of raising."""

    @pytest.fixture(autouse=True)
    def _temp_dir(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(settings, "get_app_data_dir", lambda: Path(tmp))
            yield

    def test_defaults_when_nothing_saved(self):
        assert settings.load()["appearance_mode"] == "Dark"

    def test_round_trip(self):
        settings.save(appearance_mode="Light")
        assert settings.get("appearance_mode") == "Light"

    def test_unknown_keys_ignored(self):
        settings.save(appearance_mode="Light", nonsense="x")
        assert "nonsense" not in settings.load()

    def test_corrupt_file_falls_back(self):
        settings.settings_path().write_text("{not json", encoding="utf-8")
        assert settings.load()["appearance_mode"] == "Dark"

    def test_a_file_holding_a_list_falls_back(self):
        settings.settings_path().write_text("[1, 2, 3]", encoding="utf-8")
        assert settings.load()["appearance_mode"] == "Dark"
