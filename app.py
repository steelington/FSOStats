"""Entry point for the packaged FSO Stats application.

Run from source with `python app.py`, or `python -m fsostats.gui`.

First-run unpacking of the shipped database happens inside App, behind a
splash that is a Toplevel of the main window. Doing it here instead
would need a second CTk() root, and a second root breaks image loading:
the masthead logo ends up in the wrong interpreter and tkinter reports
'image "pyimage1" doesn't exist'.
"""
from __future__ import annotations

import sys

from fsostats.gui import main

if __name__ == "__main__":
    sys.exit(main() or 0)
