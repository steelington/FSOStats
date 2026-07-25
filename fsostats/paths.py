"""Path resolution that works both running from source and packaged as a
PyInstaller onefile exe. Ported from ahstats.paths (E:\\AHSTATS).

Two distinct concerns:
  - Bundled read-only assets (icons, theme JSON) - PyInstaller extracts
    these to a temp dir each launch (sys._MEIPASS); __file__ doesn't
    reliably point there for pure-Python modules bundled in the PYZ.
  - Writable user data (the SQLite database, logs) - must NOT live under
    the onefile temp extraction dir, since that is wiped and recreated
    on every launch. FSOSTATS ships with the database pre-filled and an
    in-app Update button, so a database opened at the bundled path would
    silently lose every update the user made. See PLAN.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent

DB_FILENAME = "fsostats.db"


def resource_path(*parts: str) -> Path:
    """Path to a bundled read-only asset, relative to the fsostats
    package dir, e.g. resource_path("assets", "app_icon.ico").

    PyInstaller must bundle fsostats/assets under the same relative
    "fsostats/assets" location in the onefile archive.
    """
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS) / "fsostats"
    else:
        base = PACKAGE_DIR
    return base.joinpath(*parts)


def get_app_data_dir() -> Path:
    """Where the app keeps its writable database.

    Frozen: %LOCALAPPDATA%\\FSOStats. From source: the project root, so
    development uses the database sitting next to the code.
    """
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FSOStats"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return PACKAGE_DIR.parent


def get_database_path(progress=None) -> Path:
    """The database the app should open - always the writable copy.

    On first run the bundled gzipped database is decompressed into the
    per-user directory. **Seeding only when the target is missing is the
    whole point**: a onefile exe extracts to a temp directory that is
    wiped on every launch, so a database opened at the bundled path
    would lose everything the Update button had fetched. Re-seeding on
    every launch would do the same damage more slowly.

    `progress` is called with a short status string, since decompressing
    ~167 MB takes a couple of seconds and the window isn't up yet.
    """
    target = get_app_data_dir() / DB_FILENAME
    if target.exists():
        return target

    bundled = resource_path("data", DB_FILENAME + ".gz")
    if not bundled.exists():
        # Running from source without a prepared archive: the working
        # database next to the code is the one to use.
        return target

    import gzip
    import shutil

    if progress:
        progress("First run: unpacking the FSO database, this happens once...")
    # Written to a temporary name and renamed, so an interrupted first
    # run can't leave a half-written database that later launches would
    # accept as already seeded.
    partial = target.with_suffix(".partial")
    with gzip.open(bundled, "rb") as source, open(partial, "wb") as out:
        shutil.copyfileobj(source, out, length=4 * 1024 * 1024)
    partial.replace(target)
    return target
