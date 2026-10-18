# PyInstaller spec for FSOSTATS - onefile, windowed (no console) build.
#
# Build with:
#     python -m fsostats.prepare_db     # VACUUM, drop indexes, gzip
#     pyinstaller FSOStats.spec --clean
#
# Data layout notes:
#   - fsostats/assets and fsostats/data are bundled at those same
#     relative paths inside the archive, which is what
#     fsostats.paths.resource_path() expects.
#   - fsostats/data/fsostats.db.gz is the shipped database. It is
#     decompressed once into %LOCALAPPDATA%\FSOStats on first run and
#     only ever opened there - a onefile exe's extraction directory is
#     wiped on every launch, so anything written back to the bundled
#     path would vanish. See FINDINGS.md.
#   - customtkinter ships its own themes and fonts that PyInstaller's
#     import analysis doesn't find; collect_all() pulls them in.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

SHIPPED_DB = Path("fsostats/data/fsostats.db.gz")
if not SHIPPED_DB.exists():
    raise SystemExit(
        f"{SHIPPED_DB} is missing - run 'python -m fsostats.prepare_db' first.")

datas = [
    ("fsostats/assets", "fsostats/assets"),
    ("fsostats/data", "fsostats/data"),
]
binaries = []
hiddenimports = []

for pkg in ("customtkinter",):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # requests is the API client, which the app needs for the Update
    # button. numpy is only used by the icon build tool. bs4 and lxml
    # parsed the old site's pages and nothing imports them any more; they
    # are excluded so a dev machine that still has them installed does not
    # bundle them.
    excludes=["numpy", "pytest", "matplotlib", "PIL.ImageQt", "bs4", "lxml"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FSOStats",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="fsostats/assets/app_icon.ico",
)
