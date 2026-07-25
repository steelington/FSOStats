"""FSOSTATS - Aces High Friday Squad Operations statistics.

The application shell: masthead, pilot type-ahead, and the frame list.
Run with `python -m fsostats.gui`.

The tabs beyond Frames are deliberately empty placeholders for now - see
PLAN.md for what each one is meant to hold. Pilot and Head-to-Head are
the next pieces; Head-to-Head is the one nothing else can do, since
`log_entries.other_pilot` names both parties on kills and deaths.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from fsostats import theme
from fsostats.db import FSODB
from fsostats.grid import GridView
from fsostats.paths import get_database_path

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

        self._build_masthead()
        self._build_top_bar()
        self._build_tabs()
        self._build_status_bar()
        self._load_frames()

    def destroy(self):
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

        # Frame/campaign is the toggle every view needs, but campaign
        # grouping is only done for the 46 hand-corrected frames so far
        # (see PLAN.md), so it stays disabled rather than showing a
        # half-populated unit.
        self.scope = ctk.CTkSegmentedButton(bar, values=["Frames", "Campaigns"])
        self.scope.set("Frames")
        self.scope.configure(state="disabled")
        self.scope.pack(side="left", padx=(16, 4), pady=8)
        ctk.CTkLabel(
            bar, text="campaign grouping not built yet",
            text_color=theme.BORDER_GRAY, font=ctk.CTkFont(size=10),
        ).pack(side="left", pady=8)

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

        for name, note in (
            ("Pilot", "Career totals, aircraft flown, and the frames this pilot appeared in."),
            ("Head-to-Head", "Who a pilot killed, who killed them, and the record against any\n"
                             "named opponent across 18 years."),
            ("Squad", "Squad rosters and performance over time, from squad_stats."),
            ("Leaderboards", "All-time boards aggregated from top_entries."),
        ):
            holder = ctk.CTkFrame(self.tabs.tab(name), fg_color="transparent")
            holder.pack(expand=True)
            ctk.CTkLabel(
                holder, text=f"{name} - not built yet",
                font=ctk.CTkFont(size=16, weight="bold"), text_color=theme.TEXT_HEADING,
            ).pack(pady=(0, 6))
            ctk.CTkLabel(
                holder, text=note, text_color=theme.TEXT_BODY,
                font=ctk.CTkFont(size=12), justify="center",
            ).pack()

    # ---------------- status bar ----------------

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self, height=30)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.status = ctk.CTkLabel(
            bar, text="", text_color=theme.TEXT_BODY, font=ctk.CTkFont(size=11)
        )
        self.status.pack(side="left", padx=10, pady=6)

        # Wired up once the scrape runs on a background thread with
        # progress, as AHSTATS does for its sync.
        update = ctk.CTkButton(bar, text="Update", width=90, state="disabled")
        update.pack(side="right", padx=10, pady=4)

    # ---------------- data ----------------

    def _load_frames(self):
        rows = self.db.get_frame_summaries()
        self.frame_grid.set_rows([
            (r["event_id"], r["date"], r["name"], r["campaign"] or "",
             r["frame_no"] or "", r["pilots"], r["sorties"], r["kills"])
            for r in rows
        ])
        span = f"{rows[-1]['date']} to {rows[0]['date']}" if rows else "no data"
        self.status.configure(
            text=f"{len(rows)} frames, {span}. "
                 f"{len(self.pilot_entry._values):,} pilots."
        )

    def _on_grid_change(self, rows):
        pilots = sum(r[5] for r in rows)
        sorties = sum(r[6] for r in rows)
        kills = sum(r[7] for r in rows)
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
        # The pilot view is next; for now confirm the handle resolves so
        # the type-ahead is testable against the real 4,318 names.
        known = name in self.pilot_entry._values
        self.status.configure(
            text=f"{name}: {'known pilot' if known else 'no such pilot in the logs'} "
                 f"- pilot view not built yet."
        )


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
