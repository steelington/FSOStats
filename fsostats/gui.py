"""FSOSTATS - Aces High Friday Squad Operations statistics.

The application shell: masthead, pilot type-ahead, and the frame list.
Run with `python -m fsostats.gui`.

The tabs beyond Frames are deliberately empty placeholders for now - see
PLAN.md for what each one is meant to hold. Pilot and Head-to-Head are
the next pieces; Head-to-Head is the one nothing else can do, since
`log_entries.other_pilot` names both parties on kills and deaths.
"""
from __future__ import annotations

import queue
import tkinter as tk

import customtkinter as ctk

from fsostats import headtohead, leaderboard, pilot, squads, theme, tooltip
from fsostats.chart import TrendChart
from fsostats.db import FSODB
from fsostats.grid import GridView
from fsostats.paths import get_database_path
from fsostats.update import Updater

# Everything up to this date is scraped, hand-checked and titled, and
# ships in the database. Kept in step with scrape_fso.SCRAPE_SINCE.
SCRAPE_SINCE = "2026-07-24"

__version__ = "0.1.0"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme(str(theme.THEME_JSON_PATH))

MAX_SUGGESTIONS = 12


class AutocompleteEntry(ctk.CTkFrame):
    """A text entry that drops down matching names as you type.

    FSO handles are easy to mistype and there are over four thousand of
    them, so the pilot box is backed by the known list rather than left
    as free text. Matches that *start* with what was typed are offered
    first; the rest fall back to a substring match, which is what finds
    a pilot whose handle carries a squad prefix.

    The dropdown is a plain tk.Listbox in a borderless Toplevel: a
    CTkScrollableFrame of buttons is far heavier for what is a
    twelve-item list, and Listbox already handles keyboard traversal.
    """

    def __init__(self, parent, values=(), width=220, placeholder="", command=None,
                 **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._values: list[str] = list(values)
        self._folded: list[str] = [v.casefold() for v in self._values]
        self._command = command
        self._popup: tk.Toplevel | None = None
        self._listbox: tk.Listbox | None = None

        self.entry = ctk.CTkEntry(self, width=width, placeholder_text=placeholder)
        self.entry.pack(fill="x")
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Down>", self._focus_list)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Escape>", lambda _e: self._hide())
        self.entry.bind("<FocusOut>", self._on_focus_out)

    # -- public ---------------------------------------------------------

    def set_values(self, values) -> None:
        self._values = list(values)
        self._folded = [v.casefold() for v in self._values]

    def get(self) -> str:
        return self.entry.get().strip()

    def set(self, text: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, text)

    # -- matching -------------------------------------------------------

    def _matches(self, typed: str) -> list[str]:
        folded = typed.casefold()
        starts = [v for v, f in zip(self._values, self._folded) if f.startswith(folded)]
        if len(starts) >= MAX_SUGGESTIONS:
            return starts[:MAX_SUGGESTIONS]
        seen = set(starts)
        contains = [
            v for v, f in zip(self._values, self._folded)
            if folded in f and v not in seen
        ]
        return (starts + contains)[:MAX_SUGGESTIONS]

    # -- dropdown -------------------------------------------------------

    def _on_key(self, event) -> None:
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = self.entry.get().strip()
        if not typed:
            self._hide()
            return
        matches = self._matches(typed)
        if not matches or matches == [typed]:
            self._hide()
            return
        self._show(matches)

    def _show(self, matches) -> None:
        if self._popup is None:
            self._popup = tk.Toplevel(self)
            self._popup.wm_overrideredirect(True)
            self._popup.configure(bg=theme.BORDER_GRAY)
            self._listbox = tk.Listbox(
                self._popup,
                bg=theme.PANEL_BG, fg=theme.TEXT_BODY,
                selectbackground=theme.ACCENT_GREEN, selectforeground=theme.BG_DARK,
                highlightthickness=0, borderwidth=0, activestyle="none",
                font=("Segoe UI", 11),
            )
            self._listbox.pack(fill="both", expand=True, padx=1, pady=1)
            self._listbox.bind("<Double-Button-1>", self._on_pick)
            self._listbox.bind("<Return>", self._on_pick)
            self._listbox.bind("<Escape>", lambda _e: self._hide())

        self._listbox.delete(0, "end")
        for name in matches:
            self._listbox.insert("end", name)
        self._listbox.configure(height=min(len(matches), MAX_SUGGESTIONS))

        self.entry.update_idletasks()
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height() + 2
        self._popup.wm_geometry(f"{self.entry.winfo_width()}x{18 * len(matches) + 4}+{x}+{y}")
        self._popup.deiconify()
        self._popup.lift()

    def _hide(self) -> None:
        if self._popup is not None:
            self._popup.withdraw()

    def _on_focus_out(self, _event) -> None:
        # Losing focus *to the dropdown* must not close it, or clicking a
        # suggestion would dismiss the list before the click lands.
        if self._listbox is not None and self.focus_get() is self._listbox:
            return
        self.after(120, self._hide)

    def _focus_list(self, _event) -> None:
        if self._popup is not None and self._listbox is not None:
            self._listbox.focus_set()
            self._listbox.selection_clear(0, "end")
            self._listbox.selection_set(0)
            self._listbox.activate(0)

    def _on_pick(self, _event) -> None:
        if self._listbox is None or not self._listbox.curselection():
            return
        self.set(self._listbox.get(self._listbox.curselection()[0]))
        self._hide()
        self.entry.focus_set()
        if self._command:
            self._command(self.get())

    def _on_return(self, _event) -> None:
        self._hide()
        if self._command:
            self._command(self.get())


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"FSO Stats v{__version__}")
        self.geometry("1280x800")
        self.minsize(1100, 620)
        self.configure(fg_color=theme.BG_DARK)
        if theme.APP_ICON_ICO_PATH.exists():
            self.iconbitmap(str(theme.APP_ICON_ICO_PATH))
        theme.style_treeview()

        self.db = FSODB(get_database_path())
        self._logo_image = None  # a CTkImage must outlive the widget
        self._updater: Updater | None = None
        self._update_added = 0
        # Tracked so destroy() can cancel it - a pending 'after' firing
        # into a torn-down interpreter raises "invalid command name".
        self._update_after_id: str | None = None

        self._build_masthead()
        self._build_top_bar()
        self._build_tabs()
        self._build_status_bar()
        self._attach_tooltips()
        self._load_frames()
        self._load_squads()
        self._load_board()

    # ---------------- tooltips ----------------

    # The awkward truths about this data, put where they'll be read.
    KILL_HELP = ("Air kills, counted from the log. A kill entry names the "
                 "victim's aircraft, not the killer's - the aircraft column "
                 "resolves what the pilot was flying from their sortie.")
    ONE_SIDED_HELP = ("47 frames hold only one team's log; the other was never "
                      "posted and never turned up later. Pilots on the missing "
                      "side get no credit for a frame they flew, and side-vs-side "
                      "comparison in those frames is half-blind.")

    def _attach_tooltips(self):
        tooltip.attach(self.pilot_entry.entry, (
            "Type a handle to see a pilot's career and their record against "
            "every opponent.\n\nMatching ignores case, and the list is the "
            "4,318 pilots who actually appear in the logs, so a typo can't "
            "silently return nothing."))
        tooltip.attach(self.scope, (
            "One row of the site's data is one FRAME. An FSO campaign is "
            "normally three frames on consecutive Fridays, and people think "
            "in both units.\n\n583 frames group into 201 campaigns."))
        tooltip.attach(self.quick_filter,
                       "Filter the list below across every column at once.")
        tooltip.attach(self.update_btn, (
            "Fetch frames posted since the database was built.\n\nDiffs the "
            "site's event list and fetches only what's new - usually one "
            "frame, on a Friday. Runs in the background at three requests "
            "every three seconds; ahevents.net is a small community site."))
        tooltip.attach(self.stop_btn,
                       "Stop after the fetch in flight. Frames already saved are kept.")

        self.frame_grid.set_column_help({
            "Frame": "The frame's name, corrected where the posted title was "
                     "wrong, missing its number, or renamed mid-campaign. "
                     "Sort by ID to see the original order.",
            "Campaign": "Which FSO this frame belongs to. Grouped by title and "
                        "date; names that recur in different years carry the year.",
            "No": "Which frame of its campaign, 1-3. Read from the title where "
                  "it says, hand-checked for the 50 that don't.",
            "Sides": self.ONE_SIDED_HELP,
            "Half-logged": self.ONE_SIDED_HELP,
            "Pilots": "Pilot slots recorded for this frame, both teams.",
            "Sorties": "Take-offs, counted from sortie_start entries.",
            "Kills": self.KILL_HELP,
            "Frames": "Frames in this campaign. Usually three; fewer when a "
                      "frame's log was never rendered by the site.",
        })

        self.pilot_aircraft.set_column_help({
            "Aircraft": "What the pilot was flying, resolved by matching each "
                        "entry to the sortie it fell within.\n\n'(no sortie "
                        "recorded)' is about 4% of frames, where the log starts "
                        "without a take-off - those kills can't be pinned to a type.",
            "Kills": "Air kills scored while flying this type.",
            "Deaths": "Times shot down flying this type.",
            "K/D": "Kills divided by deaths in this aircraft.",
            "Ground": "Ground targets destroyed flying this type.",
        })
        self.pilot_frames.set_column_help({
            "Side": "The side label the event page carried. These are "
                    "unreliable - themed names, occasional duplicates, and a "
                    "few frames with the labels swapped outright - so treat it "
                    "as recorded, not as meaning Allied or Axis.",
            "Squad": "The squad this pilot flew with in this frame.",
            "Aircraft": "Every type the pilot took off in during this frame.",
        })
        self.h2h_grid.set_column_help({
            "Opponent": "Every pilot this one traded shootdowns with.",
            "Killed": "Times this pilot shot the opponent down.",
            "Killed by": "Times the opponent shot this pilot down.",
            "Diff": "Killed minus killed by.",
            "Ratio": "Killed divided by killed by.",
            "Frames": "Frames both pilots flew in - context for the record. "
                      "3-1 reads very differently over four meetings than forty.",
        })
        self.h2h_detail.set_column_help({
            "Result": "Which way the shootdown went.",
            "Aircraft lost": "The aircraft that was shot down - the opponent's "
                             "on a kill, this pilot's on a death. Blank where "
                             "only the losing side's log recorded it.",
        })
        self.squad_grid.set_column_help({
            "Squad": "Squad names are typed fresh each frame, so spellings "
                     "differing only by spacing, decoration, punctuation or "
                     "case are folded together. Similar-looking names are not: "
                     "II./JG11 and III./JG11 Black Hearts are different units.",
            "Frames": "Distinct frames flown. A squad occasionally fields teams "
                      "on both sides of one frame, which counts once here.",
            "Kills": "Air kills by the whole squad, from the totals the site "
                     "posts per frame. Spot-checked against the logs and they "
                     "agree exactly.",
            "Deaths": "Times squad pilots were shot down.",
            "K/D": "Squad kills divided by squad deaths, all frames.",
            "Ground": "Ground targets destroyed.",
            "First": "First frame this squad appears in.",
            "Last": "Most recent frame this squad appears in.",
        })
        self.squad_detail.set_column_help({
            "Side": "The side label as the event page carried it - see the "
                    "pilot view for why these can't be trusted.",
            "Pilots": "Pilots this squad fielded in this frame.",
        })
        self.board_totals.set_column_help({
            "Total": "Counted from the logs, not from the site's top lists - "
                     "those run only seven deep for pilots and three for "
                     "squads, so summing them would total the podium rather "
                     "than the career.",
            "Frames": "Frames flown, including those with nothing scored, so "
                      "the per-frame average isn't flattered.",
            "Per frame": "Total divided by frames flown.",
        })
        self.board_podium.set_column_help({
            "Wins": "Times this name topped its side's board for the frame. "
                    "Rank is per side, so every frame has two winners.",
            "Top 3": "Times placed in its side's top three.",
            "Placings": "Times appearing on a frame's list at all.",
            "Best": "Best single-frame figure the site recorded.",
        })

    def destroy(self):
        if self._updater is not None:
            self._updater.stop()
        if self._update_after_id is not None:
            try:
                self.after_cancel(self._update_after_id)
            except Exception:      # already fired, or interpreter going away
                pass
            self._update_after_id = None
        try:
            self.db.close()
        except Exception:
            pass
        super().destroy()

    # ---------------- masthead ----------------

    def _build_masthead(self):
        masthead = ctk.CTkFrame(self, fg_color=theme.ACCENT_OLIVE, corner_radius=0)
        masthead.pack(fill="x")

        if theme.MASTHEAD_LOGO_PATH.exists():
            from PIL import Image

            self._logo_image = ctk.CTkImage(
                Image.open(theme.MASTHEAD_LOGO_PATH), size=(46, 46)
            )
            ctk.CTkLabel(masthead, image=self._logo_image, text="").pack(
                side="left", padx=(14, 10), pady=8
            )

        titles = ctk.CTkFrame(masthead, fg_color="transparent")
        titles.pack(side="left", pady=8)

        row = ctk.CTkFrame(titles, fg_color="transparent")
        row.pack(anchor="w")
        ctk.CTkLabel(
            row, text="FSO STATS", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            row, text=f"v{__version__}", text_color=theme.TEXT_CREAM,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(8, 0), pady=(8, 0))

        ctk.CTkLabel(
            titles, text="Aces High - Friday Squad Operations",
            text_color=theme.TEXT_BODY, font=ctk.CTkFont(size=11),
        ).pack(anchor="w")

    # ---------------- top bar ----------------

    def _build_top_bar(self):
        bar = ctk.CTkFrame(self)
        bar.pack(fill="x", padx=10, pady=(10, 4))

        ctk.CTkLabel(bar, text="Pilot:").pack(side="left", padx=(10, 4), pady=8)
        self.pilot_entry = AutocompleteEntry(
            bar, values=self.db.get_pilot_names(), width=220,
            placeholder="start typing a handle", command=self._on_pilot_chosen,
        )
        self.pilot_entry.pack(side="left", pady=8)

        ctk.CTkButton(
            bar, text="Show", width=80, command=self._on_show,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=8, pady=8)

        # Users think in both units: one events row is one frame, and an
        # FSO campaign is normally three frames on consecutive Fridays.
        self.scope = ctk.CTkSegmentedButton(
            bar, values=["Frames", "Campaigns"], command=lambda _v: self._load_frames())
        self.scope.set("Frames")
        self.scope.pack(side="left", padx=(16, 8), pady=8)

        ctk.CTkLabel(bar, text="Find:").pack(side="left", padx=(20, 4), pady=8)
        self.quick_filter = ctk.CTkEntry(bar, width=180, placeholder_text="filter the list")
        self.quick_filter.pack(side="left", pady=8)
        self.quick_filter.bind("<KeyRelease>", self._on_quick_filter)

    # ---------------- tabs ----------------

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self, fg_color=theme.PANEL_BG)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=4)

        for name in ("Frames", "Pilot", "Head-to-Head", "Squad", "Leaderboards"):
            self.tabs.add(name)

        frames_tab = self.tabs.tab("Frames")
        self.frame_grid = GridView(
            frames_tab,
            columns=("ID", "Date", "Frame", "Campaign", "No", "Pilots", "Sorties", "Kills"),
            wide=("Frame", "Campaign"),
            height=20,
            on_change=self._on_grid_change,
        )
        # GridView's default 150px is far too narrow for frame titles,
        # which run to "Gallands Gambit: Operation Donnerkeil - Frame 3".
        for name, width in (
            ("ID", 70), ("Date", 100), ("Frame", 360), ("Campaign", 300),
            ("No", 50), ("Pilots", 80), ("Sorties", 90), ("Kills", 80),
        ):
            self.frame_grid.tree.column(name, width=width, minwidth=45)
        self.frame_grid.pack(fill="both", expand=True, padx=6, pady=6)

        self.frame_totals = ctk.CTkLabel(
            frames_tab, text="", text_color=theme.TEXT_BODY,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.frame_totals.pack(anchor="w", padx=10, pady=(0, 8))

        self._build_pilot(self.tabs.tab("Pilot"))
        self._build_head_to_head(self.tabs.tab("Head-to-Head"))
        self._build_squad(self.tabs.tab("Squad"))
        self._build_leaderboards(self.tabs.tab("Leaderboards"))

    # ---------------- pilot ----------------

    def _build_pilot(self, tab):
        self.pilot_summary = ctk.CTkLabel(
            tab, text="Choose a pilot above.", text_color=theme.TEXT_BODY,
            font=ctk.CTkFont(size=13, weight="bold"), justify="left",
        )
        self.pilot_summary.pack(anchor="w", padx=10, pady=(8, 2))

        self.pilot_outcomes = ctk.CTkLabel(
            tab, text="", text_color=theme.TEXT_BODY,
            font=ctk.CTkFont(size=12), justify="left",
        )
        self.pilot_outcomes.pack(anchor="w", padx=10, pady=(0, 6))

        self.pilot_chart = TrendChart(tab, height=150)
        self.pilot_chart.pack(fill="x", padx=10, pady=(0, 6))

        split = ctk.CTkFrame(tab, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        split.grid_columnconfigure(0, weight=3)
        split.grid_columnconfigure(1, weight=5)
        split.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            split, text="Aircraft flown", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 2))
        ctk.CTkLabel(
            split, text="Frames", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=1, sticky="w", padx=4, pady=(0, 2))

        self.pilot_aircraft = GridView(
            split, columns=("Aircraft", "Sorties", "Kills", "Deaths", "K/D", "Ground"),
            wide=("Aircraft",), height=12,
        )
        for name, width in (
            ("Aircraft", 170), ("Sorties", 70), ("Kills", 60),
            ("Deaths", 65), ("K/D", 60), ("Ground", 65),
        ):
            self.pilot_aircraft.tree.column(name, width=width, minwidth=45)
        self.pilot_aircraft.container.grid(row=1, column=0, sticky="nsew", padx=4)

        self.pilot_frames = GridView(
            split,
            columns=("Date", "Frame", "Side", "Squad", "Aircraft", "Sorties", "Kills", "Deaths"),
            wide=("Frame", "Aircraft", "Squad"), height=12,
        )
        for name, width in (
            ("Date", 90), ("Frame", 240), ("Side", 90), ("Squad", 90),
            ("Aircraft", 170), ("Sorties", 65), ("Kills", 55), ("Deaths", 60),
        ):
            self.pilot_frames.tree.column(name, width=width, minwidth=45)
        self.pilot_frames.container.grid(row=1, column=1, sticky="nsew", padx=4)

    def _load_pilot(self, name):
        career = pilot.career(self.db, name)
        if not career["frames"]:
            self.pilot_summary.configure(text=f"{name}: no frames on record.")
            self.pilot_outcomes.configure(text="")
            self.pilot_aircraft.set_rows([])
            self.pilot_frames.set_rows([])
            self.pilot_chart.plot([], message="No data.")
            return

        self.pilot_summary.configure(text=(
            f"{name}   |   {career['campaigns']} FSOs / {career['frames']} frames, "
            f"{career['first_frame']} to "
            f"{career['last_frame']}   |   {career['sorties']:,} sorties   |   "
            f"{career['kills']:,} kills, {career['deaths']:,} deaths "
            f"(K/D {career['kd']:.2f})   |   {career['ground_kills']:,} ground kills"
            f"   |   {career['aircraft_types']} aircraft types"
        ))
        # captured/crashed/exploded are how a death ended, so they are a
        # breakdown of the deaths above rather than extra losses.
        self.pilot_outcomes.configure(text=(
            f"Sortie outcomes: {career['landed']:,} landed, {career['bailed']:,} bailed, "
            f"{career['ditched']:,} ditched      "
            f"Deaths by type: {career['crashed']:,} crashed, {career['exploded']:,} exploded, "
            f"{career['captured']:,} captured, "
            f"{career['deaths'] - career['crashed'] - career['exploded'] - career['captured']:,} "
            f"shot down (no detail)"
        ))

        self.pilot_aircraft.set_rows([
            (a["plane"], a["sorties"], a["kills"], a["deaths"],
             f"{a['kd']:.2f}", a["ground_kills"])
            for a in pilot.aircraft(self.db, name)
        ])
        self.pilot_frames.set_rows([
            (f["date"], f["frame"], f["side"], f["squad"], f["planes"],
             f["sorties"], f["kills"], f["deaths"])
            for f in pilot.frames(self.db, name)
        ])

        years = pilot.yearly(self.db, name)
        self.pilot_chart.plot(
            [(int(y), kills) for y, _frames, _sorties, kills in years],
            title=f"{name} - kills per year", ylabel="kills", xlabel="Year",
            message="No data.",
        )

    # ---------------- head to head ----------------

    def _build_head_to_head(self, tab):
        self.h2h_summary = ctk.CTkLabel(
            tab, text="Choose a pilot above to see who they fought.",
            text_color=theme.TEXT_BODY, font=ctk.CTkFont(size=13, weight="bold"),
            justify="left",
        )
        self.h2h_summary.pack(anchor="w", padx=10, pady=(8, 4))

        split = ctk.CTkFrame(tab, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        split.grid_columnconfigure(0, weight=3)
        split.grid_columnconfigure(1, weight=4)
        split.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            split, text="Opponents", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 2))
        self.h2h_detail_label = ctk.CTkLabel(
            split, text="Engagements", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.h2h_detail_label.grid(row=0, column=1, sticky="w", padx=4, pady=(0, 2))

        self.h2h_grid = GridView(
            split,
            columns=("Opponent", "Killed", "Killed by", "Diff", "Ratio", "Frames"),
            wide=("Opponent",), height=16,
        )
        for name, width in (
            ("Opponent", 150), ("Killed", 70), ("Killed by", 80),
            ("Diff", 60), ("Ratio", 70), ("Frames", 70),
        ):
            self.h2h_grid.tree.column(name, width=width, minwidth=45)
        self.h2h_grid.container.grid(row=1, column=0, sticky="nsew", padx=4)
        # Selecting an opponent drives the engagement list beside it.
        self.h2h_grid.tree.bind("<<TreeviewSelect>>", self._on_opponent_selected)

        self.h2h_detail = GridView(
            split,
            columns=("Date", "Result", "Aircraft lost", "Frame"),
            wide=("Frame", "Aircraft lost"), height=16,
        )
        for name, width in (
            ("Date", 90), ("Result", 90), ("Aircraft lost", 130), ("Frame", 300),
        ):
            self.h2h_detail.tree.column(name, width=width, minwidth=45)
        self.h2h_detail.container.grid(row=1, column=1, sticky="nsew", padx=4)

    def _load_head_to_head(self, pilot):
        self._h2h_pilot = pilot
        # One pass: the opponent list feeds both the summary line and the
        # table, and computing it twice doubles the slowest query here.
        all_opponents = headtohead.opponents(self.db, pilot)
        summary = headtohead.summary(self.db, pilot, all_opponents)
        if not summary["opponents"]:
            self.h2h_summary.configure(
                text=f"{pilot}: no recorded engagements against a named pilot."
            )
            self.h2h_grid.set_rows([])
            self.h2h_detail.set_rows([])
            return

        victim = summary["top_victim"]
        nemesis = summary["top_nemesis"]
        self.h2h_summary.configure(
            text=(
                f"{pilot}   |   {summary['kills']} killed, {summary['deaths']} killed by "
                f"(ratio {summary['ratio']:.2f})   |   {summary['opponents']} opponents"
                f"   |   favourite target: {victim.name} x{victim.killed}"
                f"   |   nemesis: {nemesis.name} x{nemesis.killed_by}"
            )
        )
        self.h2h_grid.set_rows([
            (o.name, o.killed, o.killed_by, f"{o.diff:+d}", o.ratio, o.frames)
            for o in all_opponents
        ])
        self.h2h_detail.set_rows([])
        self.h2h_detail_label.configure(text="Engagements - select an opponent")

    def _on_opponent_selected(self, _event):
        selection = self.h2h_grid.tree.selection()
        if not selection or not getattr(self, "_h2h_pilot", None):
            return
        opponent = self.h2h_grid.tree.item(selection[0], "values")[0]
        rows = headtohead.record_against(self.db, self._h2h_pilot, opponent)
        self.h2h_detail.set_rows([
            (r["date"], r["direction"], r["plane"], r["frame"]) for r in rows
        ])
        self.h2h_detail_label.configure(
            text=f"{self._h2h_pilot} vs {opponent} - {len(rows)} engagements"
        )

    # ---------------- squad ----------------

    def _build_squad(self, tab):
        # wraplength or the sentence runs past both window edges.
        self.squad_summary = ctk.CTkLabel(
            tab, text="", text_color=theme.TEXT_BODY,
            font=ctk.CTkFont(size=11), justify="left", wraplength=1180,
        )
        self.squad_summary.pack(anchor="w", padx=10, pady=(8, 4))

        split = ctk.CTkFrame(tab, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        split.grid_columnconfigure(0, weight=4)
        split.grid_columnconfigure(1, weight=5)
        split.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            split, text="Squads", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 2))
        self.squad_detail_label = ctk.CTkLabel(
            split, text="Select a squad", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
            wraplength=640, justify="left",
        )
        self.squad_detail_label.grid(row=0, column=1, sticky="w", padx=4, pady=(0, 2))

        self.squad_grid = GridView(
            split,
            columns=("Squad", "Frames", "Kills", "Deaths", "K/D", "Ground", "First", "Last"),
            wide=("Squad",), height=16,
        )
        for name, width in (
            ("Squad", 220), ("Frames", 70), ("Kills", 65), ("Deaths", 70),
            ("K/D", 60), ("Ground", 70), ("First", 90), ("Last", 90),
        ):
            self.squad_grid.tree.column(name, width=width, minwidth=45)
        self.squad_grid.container.grid(row=1, column=0, sticky="nsew", padx=4)
        self.squad_grid.tree.bind("<<TreeviewSelect>>", self._on_squad_selected)

        self.squad_detail = GridView(
            split, columns=("Date", "Frame", "Side", "Pilots", "Kills", "Deaths", "Ground"),
            wide=("Frame",), height=16,
        )
        for name, width in (
            ("Date", 90), ("Frame", 260), ("Side", 90),
            ("Pilots", 60), ("Kills", 60), ("Deaths", 65), ("Ground", 65),
        ):
            self.squad_detail.tree.column(name, width=width, minwidth=45)
        self.squad_detail.container.grid(row=1, column=1, sticky="nsew", padx=4)

    def _load_squads(self):
        self._squads = squads.all_squads(self.db)
        merged = sum(1 for s in self._squads if s["spellings"] > 1)
        self.squad_summary.configure(text=(
            f"{len(self._squads)} squads. Names are typed fresh each frame, so spellings "
            f"differing only by spacing, decoration, punctuation or case are folded together "
            f"- {merged} squads here are a merge of several. Similar-looking names are NOT "
            f"merged: II./JG11 and III./JG11 Black Hearts are different units. "
            f"ZZZ_NO_SQUAD is excluded, being the site's bucket for pilots without a squad."
        ))
        self.squad_grid.set_rows([
            (s["name"], s["frames"], s["kills"], s["deaths"], f"{s['kd']:.2f}",
             s["obj"], s["first_seen"], s["last_seen"])
            for s in self._squads
        ])

    def _on_squad_selected(self, _event):
        selection = self.squad_grid.tree.selection()
        if not selection:
            return
        name = self.squad_grid.tree.item(selection[0], "values")[0]
        match = next((s for s in getattr(self, "_squads", []) if s["name"] == name), None)
        if match is None:
            return
        spellings = squads.spellings_for(self.db, match["key"])
        history = squads.history(self.db, spellings)
        roster = squads.roster(self.db, spellings)
        self.squad_detail.set_rows([
            (h["date"], h["frame"], h["side"], h["pilots"], h["kills"],
             h["deaths"], h["obj"])
            for h in history
        ])
        top = ", ".join(f"{p['pilot']} ({p['frames']})" for p in roster[:3])
        # history has one row per side, and a squad can field teams on
        # both sides of a frame, so the row count is not the frame count.
        entries = len(history)
        frames_flown = match["frames"]
        both = f" ({entries} team entries)" if entries != frames_flown else ""
        self.squad_detail_label.configure(
            text=f"{name} - {frames_flown} frames{both}, {len(roster)} pilots.  "
                 f"Most frames: {top}"
        )

    # ---------------- leaderboards ----------------

    LEADERBOARDS = {
        "Pilot kills": ("pilot", "kills", "pilot_kills"),
        "Pilot ground kills": ("pilot", "ground_kills", "pilot_obj"),
        "Squad kills": ("squad", "kills", "squad_kills"),
        "Squad ground kills": ("squad", "ground_kills", "squad_obj"),
    }

    def _build_leaderboards(self, tab):
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(8, 4))
        self.board_choice = ctk.CTkSegmentedButton(
            bar, values=list(self.LEADERBOARDS), command=lambda _v: self._load_board()
        )
        self.board_choice.set("Pilot kills")
        self.board_choice.pack(side="left")

        ctk.CTkLabel(
            tab,
            text=("Totals are counted from the logs. Podium finishes come from the "
                  "site's own per-frame top lists, which run seven deep for pilots and "
                  "three for squads - so they can't give totals, only how often someone "
                  "led. Rank is per side, so each frame has two winners, one per team."),
            text_color=theme.TEXT_BODY, font=ctk.CTkFont(size=11), justify="left",
            wraplength=1180,
        ).pack(anchor="w", padx=10, pady=(0, 6))

        split = ctk.CTkFrame(tab, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        split.grid_columnconfigure(0, weight=1)
        split.grid_columnconfigure(1, weight=1)
        split.grid_rowconfigure(1, weight=1)

        self.board_total_label = ctk.CTkLabel(
            split, text="All-time totals", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"))
        self.board_total_label.grid(row=0, column=0, sticky="w", padx=4, pady=(0, 2))
        ctk.CTkLabel(
            split, text="Podium finishes", text_color=theme.TEXT_HEADING,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=1, sticky="w", padx=4, pady=(0, 2))

        self.board_totals = GridView(
            split, columns=("#", "Name", "Total", "Frames", "Per frame"),
            wide=("Name",), height=18)
        for name, width in (("#", 50), ("Name", 210), ("Total", 80),
                            ("Frames", 75), ("Per frame", 85)):
            self.board_totals.tree.column(name, width=width, minwidth=40)
        self.board_totals.container.grid(row=1, column=0, sticky="nsew", padx=4)

        self.board_podium = GridView(
            split, columns=("#", "Name", "Wins", "Top 3", "Placings", "Best"),
            wide=("Name",), height=18)
        for name, width in (("#", 50), ("Name", 200), ("Wins", 65),
                            ("Top 3", 65), ("Placings", 80), ("Best", 60)):
            self.board_podium.tree.column(name, width=width, minwidth=40)
        self.board_podium.container.grid(row=1, column=1, sticky="nsew", padx=4)

    def _load_board(self):
        kind, metric, category = self.LEADERBOARDS[self.board_choice.get()]
        if kind == "pilot":
            rows = leaderboard.pilot_totals(self.db, metric)
            totals = [(i, r["pilot"], r["total"], r["frames"], f"{r['per_frame']:.2f}")
                      for i, r in enumerate(rows, 1)]
        else:
            rows = leaderboard.squad_totals(self.db, metric)
            totals = [(i, r["squad"], r["total"], r["frames"], f"{r['per_frame']:.2f}")
                      for i, r in enumerate(rows, 1)]
        self.board_totals.set_rows(totals)
        self.board_total_label.configure(
            text=f"All-time totals - {self.board_choice.get().lower()}")
        self.board_podium.set_rows([
            (i, r["name"], r["wins"], r["top3"], r["placings"], r["best"])
            for i, r in enumerate(leaderboard.podium(self.db, category), 1)
        ])

    # ---------------- status bar ----------------

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self, height=30)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.status = ctk.CTkLabel(
            bar, text="", text_color=theme.TEXT_BODY, font=ctk.CTkFont(size=11)
        )
        self.status.pack(side="left", padx=10, pady=6)

        self.update_btn = ctk.CTkButton(
            bar, text="Update", width=90, command=self._start_update)
        self.update_btn.pack(side="right", padx=10, pady=4)
        self.stop_btn = ctk.CTkButton(
            bar, text="Stop", width=70, command=self._stop_update, state="disabled",
            fg_color=theme.PANEL_BG_ALT, hover_color=theme.ACCENT_RED)
        self.stop_btn.pack(side="right", padx=(0, 4), pady=4)
        self.progress = ctk.CTkProgressBar(bar, width=200)
        self.progress.set(0)
        self.progress.pack(side="right", padx=10, pady=4)
        self.progress.pack_forget()      # only while an update runs

    # ---------------- update ----------------

    def _start_update(self):
        if self._updater is not None and self._updater.running:
            return
        self._updater = Updater(
            since=SCRAPE_SINCE, known_event_ids=self.db.get_fetched_event_ids())
        self._update_added = 0
        self.update_btn.configure(state="disabled", text="Updating")
        self.stop_btn.configure(state="normal")
        self.progress.pack(side="right", padx=10, pady=4)
        self.progress.set(0)
        self.status.configure(text="Contacting ahevents.net...")
        self._updater.start()
        self._poll_update()

    def _stop_update(self):
        if self._updater is not None:
            self._updater.stop()
            self.status.configure(text="Stopping after the current fetch...")

    def _poll_update(self):
        """Drain the worker's queue on the UI thread.

        Every database write happens here, never on the worker: FSODB
        holds one sqlite connection and isn't thread-safe.
        """
        finished = False
        while True:
            try:
                message = self._updater.messages.get_nowait()
            except queue.Empty:
                break

            if message.kind == "event":
                try:
                    self.db.save_event(message.event)
                    self._update_added += 1
                except Exception as error:   # noqa: BLE001 - one bad frame must not stop the run
                    self.status.configure(
                        text=f"Save failed: {type(error).__name__}: {error}")
                else:
                    self.status.configure(text=message.text)
            elif message.kind == "error":
                self.status.configure(text=f"Update failed - {message.text.splitlines()[0]}")
                finished = True
            elif message.kind == "done":
                self.status.configure(text=message.text)
                finished = True
            else:
                self.status.configure(text=message.text)

            if message.fraction:
                self.progress.set(message.fraction)

        if finished or not self._updater.running:
            self._finish_update()
            return
        self._update_after_id = self.after(200, self._poll_update)

    def _finish_update(self):
        self._update_after_id = None
        self.update_btn.configure(state="normal", text="Update")
        self.stop_btn.configure(state="disabled")
        self.progress.pack_forget()

        if self._update_added:
            # New frames arrive untitled and ungrouped; this names them
            # and re-runs the campaign clustering over the whole set.
            self.db.apply_campaign_metadata()
            self.pilot_entry.set_values(self.db.get_pilot_names())
            self._load_frames()
            self._load_squads()
            self._load_board()
            self.status.configure(
                text=f"Added {self._update_added} frame(s). Lists refreshed.")

    # ---------------- data ----------------

    def _load_frames(self):
        if self.scope.get() == "Campaigns":
            self._scope_unit = "campaigns"
            rows = self.db.get_campaign_summaries()
            self.frame_grid.set_columns(
                ("Campaign", "Frames", "First", "Last", "Half-logged",
                 "Pilots", "Sorties", "Kills"),
                wide=("Campaign",))
            for name, width in (
                ("Campaign", 360), ("Frames", 70), ("First", 95), ("Last", 95),
                ("Half-logged", 95), ("Pilots", 70), ("Sorties", 80), ("Kills", 70),
            ):
                self.frame_grid.tree.column(name, width=width, minwidth=45)
            self.frame_grid.set_rows([
                (r["name"], r["frames"], r["first_date"], r["last_date"],
                 r["one_sided"] or "", r["pilots"], r["sorties"], r["kills"])
                for r in rows
            ])
            span = f"{rows[-1]['first_date']} to {rows[0]['last_date']}" if rows else "no data"
            self.status.configure(
                text=f"{len(rows)} campaigns, {span}. "
                     f"{len(self.pilot_entry._values):,} pilots.")
            return

        self._scope_unit = "frames"
        rows = self.db.get_frame_summaries()
        self.frame_grid.set_columns(
            ("ID", "Date", "Frame", "Campaign", "No", "Sides", "Pilots", "Sorties", "Kills"),
            wide=("Frame", "Campaign"))
        for name, width in (
            ("ID", 70), ("Date", 100), ("Frame", 300), ("Campaign", 250),
            ("No", 45), ("Sides", 60), ("Pilots", 70), ("Sorties", 80), ("Kills", 70),
        ):
            self.frame_grid.tree.column(name, width=width, minwidth=40)
        self.frame_grid.set_rows([
            (r["event_id"], r["date"], r["name"], r["campaign"] or "",
             r["frame_no"] or "",
             # 47 frames hold only one team's log; the other was never
             # posted and never turned up later.
             "one" if r["one_sided"] else "both",
             r["pilots"], r["sorties"], r["kills"])
            for r in rows
        ])
        span = f"{rows[-1]['date']} to {rows[0]['date']}" if rows else "no data"
        half = sum(1 for r in rows if r["one_sided"])
        self.status.configure(
            text=f"{len(rows)} frames, {span}. {len(self.pilot_entry._values):,} pilots. "
                 f"{half} frames have only one side's log."
        )

    def _on_grid_change(self, rows):
        if getattr(self, "_scope_unit", "frames") == "campaigns":
            frames = sum(r[1] for r in rows)
            pilots = sum(r[5] for r in rows)
            sorties = sum(r[6] for r in rows)
            kills = sum(r[7] for r in rows)
            self.frame_totals.configure(
                text=f"{len(rows)} campaigns shown   |   {frames:,} frames   |   "
                     f"{pilots:,} pilot slots   |   {sorties:,} sorties   |   {kills:,} kills")
            return
        pilots = sum(r[6] for r in rows)
        sorties = sum(r[7] for r in rows)
        kills = sum(r[8] for r in rows)
        self.frame_totals.configure(
            text=f"{len(rows)} frames shown   |   {pilots:,} pilot slots   |   "
                 f"{sorties:,} sorties   |   {kills:,} kills"
        )

    def _on_quick_filter(self, _event):
        self.frame_grid.set_quick_filter(self.quick_filter.get())

    def _on_pilot_chosen(self, name):
        self._on_show()

    def _on_show(self):
        name = self.pilot_entry.get()
        if not name:
            self.status.configure(text="Type a pilot handle first.")
            return
        # Handles fold case throughout, so the typed spelling need not
        # match the logs exactly.
        folded = {v.casefold() for v in self.pilot_entry._values}
        if name.casefold() not in folded:
            self.status.configure(text=f"{name}: no such pilot in the logs.")
            return
        self.status.configure(text=f"{name}: loading...")
        self.update_idletasks()
        self._load_pilot(name)
        self._load_head_to_head(name)
        self.tabs.set("Pilot")
        self.status.configure(text=f"{name}: loaded.")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
