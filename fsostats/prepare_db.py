"""Prepare the database for shipping: VACUUM, then gzip.

Run before building:  python -m fsostats.prepare_db

**VACUUM has to be a build step, not a one-off.** `save_event` deletes
and re-inserts an event's rows, so the file refragments on every
rescrape and every press of the Update button. It also never shrinks on
its own once rows are removed.

The shipped copy is gzipped rather than raw. PyInstaller compresses
embedded data anyway, but doing it here means the size is known and
checked before the build, and the seed step decompresses once into the
writable copy - which is where the app opens it (see paths.py).

**Indexes are dropped before shipping.** They are more than half the
file - 167 MB with them, 78 MB without - and `FSODB`'s SCHEMA recreates
every one on first open, which takes about 2.5 seconds. That halves the
download, from 37.5 MB to 18.6 MB, for a one-off wait the user sees
once. It also guarantees the shipped database carries exactly the
indexes SCHEMA declares, rather than whatever a development machine
happened to accumulate.
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "fsostats.db"
SHIPPED = Path(__file__).resolve().parent / "data" / "fsostats.db.gz"

# Gzip's slowest, smallest setting. This runs once per build and saves
# megabytes off every download, so the seconds are well spent.
COMPRESSION = 9


def _mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def main() -> int:
    if not SOURCE.exists():
        print(f"No database at {SOURCE}", file=sys.stderr)
        return 1

    before = _mb(SOURCE)
    print(f"{SOURCE.name}: {before:.1f} MB")

    # Build against a copy: the working database keeps its indexes.
    staged = SOURCE.with_suffix(".ship.db")
    if staged.exists():
        staged.unlink()
    print("copying...", flush=True)
    shutil.copy2(SOURCE, staged)

    conn = sqlite3.connect(staged)
    indexes = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")]
    print(f"dropping {len(indexes)} indexes (SCHEMA rebuilds them on first open)...",
          flush=True)
    for name in indexes:
        conn.execute(f"DROP INDEX {name}")
    conn.commit()

    print("VACUUM...", flush=True)
    started = time.time()
    conn.execute("VACUUM")
    conn.commit()
    conn.close()
    vacuumed = _mb(staged)
    print(f"  {before:.1f} -> {vacuumed:.1f} MB in {time.time()-started:.0f}s "
          f"({before - vacuumed:.1f} MB saved)")

    SHIPPED.parent.mkdir(parents=True, exist_ok=True)
    print(f"gzip -{COMPRESSION}...", flush=True)
    started = time.time()
    with open(staged, "rb") as raw, gzip.open(SHIPPED, "wb", compresslevel=COMPRESSION) as out:
        shutil.copyfileobj(raw, out, length=4 * 1024 * 1024)
    print(f"  {vacuumed:.1f} -> {_mb(SHIPPED):.1f} MB in {time.time()-started:.0f}s")
    staged.unlink()
    print(f"\nWrote {SHIPPED} ({_mb(SHIPPED):.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
