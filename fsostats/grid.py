"""A Treeview wrapper that adds sorting, filtering and a totals footer.

Ported unchanged in behaviour from ahstats.grid (E:\\AHSTATS):

  * click a heading to sort, click again to reverse
  * right-click a heading to filter that column (text match, or a
    numeric range for number columns)
  * a quick-filter box that matches across every column at once
  * an optional running-totals footer

Rows are held here rather than in the widget, since filtering needs the
full set to fall back to.
"""
from __future__ import annotations

import re
from tkinter import ttk

import customtkinter as ctk

from fsostats import theme, tooltip


def sort_key(value):
    """Sort a cell numerically when it looks like a number, else as text.

    Returns a tuple so that mixed columns still compare cleanly, and
    blanks always sort last rather than ahead of zero."""
    text = str(value).strip()
    if not text:
        return (1, 0.0, "")
    try:
        return (0, float(text.replace(",", "").replace("%", "")), "")
    except ValueError:
        # Zero-padded "hh:mm:ss" durations sort correctly as plain text.
        return (0, 0.0, text.casefold())


def as_number(value):
    """The numeric value of a cell, or None if it isn't a number."""
    try:
        return float(str(value).strip().replace(",", "").replace("%", ""))
    except (ValueError, AttributeError):
        return None


class ColumnFilter:
    """One column's filter: a substring match and/or a numeric range."""

    def __init__(self, text="", low=None, high=None):
        self.text = text
        self.low = low
        self.high = high

    def active(self) -> bool:
        return bool(self.text) or self.low is not None or self.high is not None

    def matches(self, value) -> bool:
        if self.text and self.text.casefold() not in str(value).casefold():
            return False
        if self.low is not None or self.high is not None:
            number = as_number(value)
            if number is None:
                return False
            if self.low is not None and number < self.low:
                return False
            if self.high is not None and number > self.high:
                return False
        return True


class GridView:
    """A sortable, filterable Treeview with scrollbars.

    Callers set columns and rows; this handles display. `on_change` fires
    whenever the visible row set changes, so a totals footer can follow
    the filters."""

    def __init__(self, parent, columns=(), height=10, wide=(), on_change=None,
                 toolbar=True, title="grid"):
        self.on_change = on_change
        self.title = title
        self._rows: list[tuple] = []
        self._visible: list[tuple] = []
        self._wide: tuple = tuple(wide)
        self._filters: dict[str, ColumnFilter] = {}
        self._quick = ""
        self._sort: tuple[str, bool] | None = None

        self.container = ctk.CTkFrame(parent, fg_color="transparent")

        # Every grid carries its own quick filter and export, so the
        # behaviour is the same wherever you are rather than only on the
        # one grid that happened to get a filter box.
        row = 0
        if toolbar:
            bar = ctk.CTkFrame(self.container, fg_color="transparent")
            bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3))
            self.quick_entry = ctk.CTkEntry(
                bar, width=170, height=26, placeholder_text="filter...")
            self.quick_entry.pack(side="left")
            self.quick_entry.bind(
                "<KeyRelease>", lambda _e: self.set_quick_filter(self.quick_entry.get()))
            ctk.CTkButton(
                bar, text="Clear", width=54, height=26, command=self._clear_all,
                fg_color=theme.PANEL_BG_ALT, hover_color=theme.BORDER_GRAY, text_color=theme.TEXT_BODY,
            ).pack(side="left", padx=4)
            self.export_btn = ctk.CTkButton(
                bar, text="Export CSV", width=90, height=26, command=self.export_csv,
                fg_color=theme.PANEL_BG_ALT, hover_color=theme.ACCENT_OLIVE_HOVER, text_color=theme.TEXT_BODY)
            self.export_btn.pack(side="left")
            self.count_label = ctk.CTkLabel(
                bar, text="", text_color=theme.BORDER_GRAY,
                font=ctk.CTkFont(size=10))
            self.count_label.pack(side="left", padx=8)
            row = 1
        else:
            self.quick_entry = None
            self.count_label = None

        self._tree_row = row
        self.tree = ttk.Treeview(self.container, show="headings", height=height)
        vsb = ttk.Scrollbar(self.container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self.container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=row, column=0, sticky="nsew")
        vsb.grid(row=row, column=1, sticky="ns")
        hsb.grid(row=row + 1, column=0, sticky="ew")
        self.container.grid_rowconfigure(row, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        theme.configure_zebra_tags(self.tree)
        # Row tags hold flat colours, so they have to be re-set and the
        # rows re-inserted when the app switches light/dark.
        theme.on_mode_change(self._apply_mode)
        self.tree.bind("<Button-3>", self._on_right_click)

        # Column help, shown when the pointer rests on a heading. The
        # tooltip text is resolved lazily so one Tooltip can serve every
        # column - it asks which heading the pointer is over at the
        # moment it fires.
        self._column_help: dict[str, str] = {}
        tooltip.attach(self.tree, self._help_under_pointer)

        if columns:
            self.set_columns(columns, wide=wide)

    # -- layout -------------------------------------------------------

    def _apply_mode(self) -> None:
        """Re-tag every row for the current appearance mode.

        Raises once the grid is destroyed, which is how theme drops a
        dead listener - see theme.on_mode_change."""
        theme.configure_zebra_tags(self.tree)
        self._rebuild()

    def pack(self, **kwargs):
        self.container.pack(**kwargs)

    @property
    def columns(self) -> tuple:
        return tuple(self.tree.cget("columns"))

    def set_columns(self, columns, wide=()) -> None:
        """Point the grid at a new set of columns, clearing rows, filters
        and sort - they belong to the old columns."""
        self._wide = tuple(wide) or self._wide
        self._rows = []
        self._filters = {}
        self._sort = None
        self.tree.delete(*self.tree.get_children())
        self.tree.configure(columns=list(columns))
        for name in columns:
            self.tree.column(name, width=150 if name in self._wide else 90, anchor="center")
            self.tree.heading(name, command=lambda c=name: self.sort_by(c))
        if columns:
            self.tree.column(columns[0], width=60, anchor="center")
        for name in self._wide:
            if name in columns:
                self.tree.column(name, anchor="w")
        self._refresh_headings()

    # -- column help --------------------------------------------------

    def set_column_help(self, help_by_column: dict) -> None:
        """Hover text per column heading. Survives set_columns()."""
        self._column_help = dict(help_by_column)

    def _help_under_pointer(self) -> str:
        """What the pointer is over, as tooltip text.

        Returns the column's own help over a heading, and a hint about
        sorting and filtering when the column has none, so the right
        click menu is discoverable at all.
        """
        try:
            x = self.tree.winfo_pointerx() - self.tree.winfo_rootx()
            y = self.tree.winfo_pointery() - self.tree.winfo_rooty()
            if self.tree.identify_region(x, y) != "heading":
                return ""
            index = int(self.tree.identify_column(x).lstrip("#")) - 1
            name = self.columns[index]
        except (ValueError, IndexError, Exception):
            return ""
        help_text = self._column_help.get(name)
        footer = "Click to sort, right-click to filter."
        return f"{name}\n\n{help_text}\n\n{footer}" if help_text else footer

    # -- data ---------------------------------------------------------

    def set_rows(self, rows) -> None:
        self._rows = [tuple(r) for r in rows]
        self._rebuild()

    def visible_rows(self) -> list[tuple]:
        """The rows currently passing the filters, in display order."""
        return self._visible

    def _passes(self, row) -> bool:
        if self._quick and not any(self._quick in str(v).casefold() for v in row):
            return False
        for column, flt in self._filters.items():
            if column not in self.columns:
                continue
            if not flt.matches(row[self.columns.index(column)]):
                return False
        return True

    def _rebuild(self) -> None:
        rows = [r for r in self._rows if self._passes(r)]
        if self._sort:
            column, reverse = self._sort
            if column in self.columns:
                index = self.columns.index(column)
                rows.sort(key=lambda r: sort_key(r[index]), reverse=reverse)
        self._visible = rows

        self.tree.delete(*self.tree.get_children())
        for position, row in enumerate(rows):
            self.tree.insert("", "end", values=row, tags=theme.zebra_tag(position))
        self._refresh_headings()
        if self.count_label is not None:
            total = len(self._rows)
            shown = len(rows)
            self.count_label.configure(
                text=f"{shown:,} rows" if shown == total else f"{shown:,} of {total:,}")
        if self.on_change:
            self.on_change(rows)

    # -- sorting ------------------------------------------------------

    def sort_by(self, column: str) -> None:
        if self._sort and self._sort[0] == column:
            self._sort = (column, not self._sort[1])
        else:
            self._sort = (column, False)
        self._rebuild()

    def _refresh_headings(self) -> None:
        """Redraw heading captions with sort and filter indicators."""
        for name in self.columns:
            caption = name
            if self._sort and self._sort[0] == name:
                caption += " ▼" if self._sort[1] else " ▲"
            if name in self._filters and self._filters[name].active():
                caption += " ⚑"
            self.tree.heading(name, text=caption)

    # -- filtering ----------------------------------------------------

    def set_quick_filter(self, text: str) -> None:
        self._quick = (text or "").strip().casefold()
        self._rebuild()

    def clear_filters(self) -> None:
        self._filters = {}
        self._quick = ""
        self._rebuild()

    def _clear_all(self) -> None:
        if self.quick_entry is not None:
            self.quick_entry.delete(0, "end")
        self.clear_filters()

    # -- export -------------------------------------------------------

    def export_csv(self, path=None) -> str | None:
        """Write the rows currently visible - filters and sort applied,
        since that's what the user is looking at - to a CSV file."""
        import csv
        from tkinter import filedialog

        if path is None:
            safe = re.sub(r"[^A-Za-z0-9]+", "_", self.title).strip("_").lower()
            path = filedialog.asksaveasfilename(
                parent=self.container,
                title=f"Export {self.title}",
                defaultextension=".csv",
                initialfile=f"fsostats_{safe}.csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
        if not path:
            return None
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.columns)
            writer.writerows(self._visible)
        return path

    def _on_right_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "heading":
            return
        column_id = self.tree.identify_column(event.x)
        try:
            name = self.columns[int(column_id.lstrip("#")) - 1]
        except (ValueError, IndexError):
            return
        self._open_filter_dialog(name)

    def _column_is_numeric(self, name: str) -> bool:
        index = self.columns.index(name)
        values = [r[index] for r in self._rows[:40]]
        numbers = [as_number(v) for v in values if str(v).strip()]
        return bool(numbers) and all(n is not None for n in numbers)

    def _open_filter_dialog(self, name: str) -> None:
        existing = self._filters.get(name, ColumnFilter())
        numeric = self._column_is_numeric(name)

        dialog = ctk.CTkToplevel(self.container)
        dialog.title(f"Filter: {name}")
        dialog.geometry("320x220" if numeric else "320x160")
        dialog.transient(self.container.winfo_toplevel())
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"Filter '{name}'", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(12, 6)
        )
        ctk.CTkLabel(dialog, text="Contains:").pack(anchor="w", padx=12)
        text_entry = ctk.CTkEntry(dialog, width=280)
        text_entry.insert(0, existing.text)
        text_entry.pack(padx=12, pady=(0, 8))

        low_entry = high_entry = None
        if numeric:
            range_row = ctk.CTkFrame(dialog, fg_color="transparent")
            range_row.pack(fill="x", padx=12, pady=(0, 8))
            ctk.CTkLabel(range_row, text="Min:").pack(side="left")
            low_entry = ctk.CTkEntry(range_row, width=90)
            low_entry.pack(side="left", padx=(4, 12))
            ctk.CTkLabel(range_row, text="Max:").pack(side="left")
            high_entry = ctk.CTkEntry(range_row, width=90)
            high_entry.pack(side="left", padx=4)
            if existing.low is not None:
                low_entry.insert(0, str(existing.low))
            if existing.high is not None:
                high_entry.insert(0, str(existing.high))

        def apply_filter():
            flt = ColumnFilter(text=text_entry.get().strip())
            if numeric:
                flt.low = as_number(low_entry.get())
                flt.high = as_number(high_entry.get())
            if flt.active():
                self._filters[name] = flt
            else:
                self._filters.pop(name, None)
            self._rebuild()
            dialog.destroy()

        def clear_filter():
            self._filters.pop(name, None)
            self._rebuild()
            dialog.destroy()

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(pady=10)
        ctk.CTkButton(buttons, text="Apply", command=apply_filter, width=90).pack(side="left", padx=4)
        ctk.CTkButton(
            buttons, text="Clear", command=clear_filter, width=90,
            fg_color=theme.PANEL_BG_ALT, hover_color=theme.BORDER_GRAY, text_color=theme.TEXT_BODY,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            buttons, text="Cancel", command=dialog.destroy, width=90,
            fg_color=theme.PANEL_BG_ALT, hover_color=theme.BORDER_GRAY, text_color=theme.TEXT_BODY,
        ).pack(side="left", padx=4)

        text_entry.focus_set()
        dialog.bind("<Return>", lambda _e: apply_filter())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
