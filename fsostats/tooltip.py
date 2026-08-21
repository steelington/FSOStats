"""Hover tooltips.

This data needs them more than most apps do. A column headed "Kills"
raises a fair question - kills of what, counted how, over which frames -
and the answers here are not obvious: a kill row names the victim's
aircraft, 47 frames hold only one side's log, podium finishes are ranked
per side. Rather than crowd the window with captions, the explanation
sits under the pointer.

tkinter has no tooltip widget, so this is a borderless Toplevel shown
after a short delay and cancelled on leave.
"""
from __future__ import annotations

import tkinter as tk

from fsostats import theme

DELAY_MS = 450
MAX_WIDTH = 380


def _bindable(widget, _depth=0):
    """The widgets that can actually carry the hover bindings.

    Several customtkinter widgets are composites whose own bind() raises
    NotImplementedError - CTkSegmentedButton is one - so the binding
    goes onto the real tkinter widgets underneath instead. Binding every
    child also means the tooltip survives the pointer moving between a
    frame and the label drawn on it.
    """
    try:
        widget.bind("<Enter>", lambda _e: None, add="+")
        return [widget]
    except (NotImplementedError, AttributeError, tk.TclError):
        pass
    if _depth > 3:
        return []
    found = []
    for child in widget.winfo_children():
        found.extend(_bindable(child, _depth + 1))
    return found


class Tooltip:
    """Attach hover text to any widget.

    `text` may be a callable, for tooltips whose content depends on
    what the pointer is over - see GridView's column help.
    """

    def __init__(self, widget, text, delay=DELAY_MS):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._window: tk.Toplevel | None = None

        for target in _bindable(widget):
            target.bind("<Enter>", self._schedule, add="+")
            target.bind("<Leave>", self._hide, add="+")
            # A click means the user is busy; a tooltip appearing over
            # the thing they just pressed is only in the way.
            target.bind("<ButtonPress>", self._hide, add="+")

    # -- scheduling -----------------------------------------------------

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:      # widget already gone
                pass
            self._after_id = None

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None

    # -- display --------------------------------------------------------

    def _resolve(self) -> str:
        text = self.text() if callable(self.text) else self.text
        return (text or "").strip()

    def _show(self):
        text = self._resolve()
        if not text or self._window is not None:
            return
        try:
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 20
        except tk.TclError:
            return

        self._window = tk.Toplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.configure(bg=theme.color(theme.BORDER_GRAY))
        label = tk.Label(
            self._window, text=text, justify="left",
            bg=theme.color(theme.PANEL_BG_ALT), fg=theme.color(theme.TEXT_BODY),
            font=("Segoe UI", 9), wraplength=MAX_WIDTH,
            padx=8, pady=5, bd=0,
        )
        label.pack(padx=1, pady=1)
        self._window.wm_geometry(f"+{x}+{y}")

        # Keep it on screen when hovering near the right or bottom edge.
        self._window.update_idletasks()
        w, h = self._window.winfo_width(), self._window.winfo_height()
        screen_w = self._window.winfo_screenwidth()
        screen_h = self._window.winfo_screenheight()
        if x + w > screen_w:
            x = max(0, screen_w - w - 8)
        if y + h > screen_h:
            y = max(0, y - h - 40)
        self._window.wm_geometry(f"+{x}+{y}")

    def update_text(self, text):
        self.text = text


def attach(widget, text, delay=DELAY_MS) -> Tooltip:
    """Convenience wrapper: Tooltip(widget, text) in one call."""
    return Tooltip(widget, text, delay)
