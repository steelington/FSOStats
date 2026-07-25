"""Colour palette, shared with AHSTATS (E:\\AHSTATS ahstats/theme.py).

The palette came from hitechcreations.com's own site CSS, so both apps
read as companions to the game's website rather than generic dark-mode
tools. Keep the two in step - if one changes, change both.
"""
from __future__ import annotations

from tkinter import ttk

from fsostats.paths import resource_path

BG_DARK = "#0f1419"           # window background
PANEL_BG = "#1a1f26"          # panels, even grid rows
PANEL_BG_ALT = "#242b33"      # odd grid rows
BORDER_GRAY = "#2e3440"       # borders, scrollbar trough
TEXT_BODY = "#d8dee9"
TEXT_HEADING = "#eceff4"
TEXT_CREAM = "#e5e9f0"        # grid heading text on olive

ACCENT_GREEN = "#10b981"      # primary actions, selection
ACCENT_GREEN_HOVER = "#059669"
ACCENT_OLIVE = "#4a5a42"      # masthead and grid headings - military feel
ACCENT_OLIVE_HOVER = "#5f7254"
ACCENT_BLUE = "#3b82f6"       # informational
ACCENT_RED = "#ef4444"        # stop / danger

# The app icon's own amber, for the frame/campaign accents that shouldn't
# compete with the green primary action colour.
ACCENT_AMBER = "#fdc858"

THEME_JSON_PATH = resource_path("assets", "fso_theme.json")
APP_ICON_ICO_PATH = resource_path("assets", "app_icon.ico")
APP_ICON_PNG_PATH = resource_path("assets", "app_icon.png")
MASTHEAD_LOGO_PATH = resource_path("assets", "masthead_logo.png")


def style_treeview() -> None:
    """ttk.Treeview isn't a customtkinter widget, so it doesn't pick up
    the CTk theme automatically - style it to match by hand."""
    import tkinter.font as tkfont

    style = ttk.Style()
    # 'clam' honours colour overrides on Windows; the default 'vista'
    # theme mostly ignores them.
    style.theme_use("clam")

    tree_font = tkfont.Font(family="Segoe UI", size=11)
    heading_font = tkfont.Font(family="Segoe UI", size=12, weight="bold")

    style.configure(
        "Treeview",
        background=PANEL_BG,
        fieldbackground=PANEL_BG,
        foreground=TEXT_BODY,
        borderwidth=0,
        rowheight=28,
        font=tree_font,
    )
    style.map(
        "Treeview",
        background=[("selected", ACCENT_GREEN)],
        foreground=[("selected", BG_DARK)],
    )
    style.configure(
        "Treeview.Heading",
        background=ACCENT_OLIVE,
        foreground=TEXT_CREAM,
        borderwidth=0,
        relief="flat",
        font=heading_font,
    )
    style.map("Treeview.Heading", background=[("active", ACCENT_OLIVE_HOVER)])

    # Scrollbars are ttk too, so they need the same hand-styling or they
    # come out in default light grey against the dark grids.
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=BORDER_GRAY,
            troughcolor=BG_DARK,
            bordercolor=BG_DARK,
            arrowcolor=TEXT_BODY,
            borderwidth=0,
        )
        style.map(f"{orient}.TScrollbar", background=[("active", ACCENT_OLIVE_HOVER)])


def configure_zebra_tags(tree) -> None:
    """Call once per Treeview right after creation. Pass tags=zebra_tag(i)
    to each .insert() so alternating rows get a slightly different shade,
    which makes it easier to track a row across columns."""
    tree.tag_configure("even", background=PANEL_BG)
    tree.tag_configure("odd", background=PANEL_BG_ALT)


def zebra_tag(index: int) -> tuple:
    return ("even",) if index % 2 == 0 else ("odd",)
