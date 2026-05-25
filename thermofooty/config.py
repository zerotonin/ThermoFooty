# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — config                                            ║
# ║  « single source of truth for data-root + ingestion settings »   ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  All paths below derive from THERMOFOOTY_DATA_ROOT (env var) so  ║
# ║  the same codebase runs on Bart's workstation (DATADRIVE1),      ║
# ║  Aoraki HPC, or a collaborator's machine.  Default falls back    ║
# ║  to the in-repo ``data/`` symlink (which on Bart's box points    ║
# ║  at /media/geuba03p/DATADRIVE1/ThermoFooty).                     ║
# ║                                                                  ║
# ║  Mounting policy: import-time check ensures the data root        ║
# ║  exists and is writeable.  Loud failure beats silent fall-back   ║
# ║  to the wrong drive.                                             ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Data-root resolution + ingestion settings for ThermoFooty."""

from __future__ import annotations

import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
#  Data-root resolution
# ─────────────────────────────────────────────────────────────────

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

#: Default data root, resolved at import time.  Override with the
#: ``THERMOFOOTY_DATA_ROOT`` environment variable on machines where
#: DATADRIVE1 isn't mounted.
DATA_ROOT: Path = Path(
    os.environ.get("THERMOFOOTY_DATA_ROOT", REPO_ROOT / "data")
).resolve()

# ─────────────────────────────────────────────────────────────────
#  Standard sub-directories  (mirrors README "Data layout" table)
# ─────────────────────────────────────────────────────────────────

DB_DIR: Path = DATA_ROOT / "db"
RAW_DIR: Path = DATA_ROOT / "raw"
CACHE_DIR: Path = DATA_ROOT / "cache"
DERIVED_DIR: Path = DATA_ROOT / "derived"
LOGS_DIR: Path = DATA_ROOT / "logs"

# ─────────────────────────────────────────────────────────────────
#  Per-source raw + cache subdirectories
# ─────────────────────────────────────────────────────────────────

RAW_FOOTBALL_DATA_UK: Path = RAW_DIR / "football_data_uk"
RAW_FBREF_HTML: Path = RAW_DIR / "fbref_html"
RAW_HOME_OFFICE: Path = RAW_DIR / "home_office_pdfs"
RAW_ZIS: Path = RAW_DIR / "zis_jahresberichte"
RAW_STADIA: Path = RAW_DIR / "stadia"
RAW_HADCET: Path = RAW_DIR / "observatories" / "hadcet"

CACHE_METEOSTAT: Path = CACHE_DIR / "meteostat"
CACHE_ERA5: Path = CACHE_DIR / "era5"
CACHE_TWENTYCR: Path = CACHE_DIR / "twentycr"
CACHE_FBREF_PARSED: Path = CACHE_DIR / "fbref_parsed"

# ─────────────────────────────────────────────────────────────────
#  Canonical SQLite database file
# ─────────────────────────────────────────────────────────────────

SQLITE_PATH: Path = DB_DIR / "thermofooty.sqlite"
SCHEMA_SQL_PATH: Path = REPO_ROOT / "db" / "schema.sql"
MIGRATIONS_DIR: Path = REPO_ROOT / "db" / "migrations"

# ─────────────────────────────────────────────────────────────────
#  Validation  « loud failure beats silent fall-back »
# ─────────────────────────────────────────────────────────────────


def assert_data_root_ready() -> None:
    """Sanity-check that DATA_ROOT exists and is writeable.

    Call this from any CLI entry point that touches data.  Cheap to
    run repeatedly.  Raises a precise ``RuntimeError`` if the mount
    is missing — the typical cause is DATADRIVE1 not mounted at boot.
    """
    if not DATA_ROOT.exists():
        raise RuntimeError(
            f"THERMOFOOTY_DATA_ROOT = {DATA_ROOT} does not exist. "
            f"On Bart's workstation this usually means /media/geuba03p/"
            f"DATADRIVE1 is not mounted (check `mount | grep DATADRIVE1`). "
            f"On other machines, set THERMOFOOTY_DATA_ROOT to a writeable "
            f"path with the expected sub-directory layout (see README "
            f"'Data layout (on DATADRIVE1)' for the full tree)."
        )
    if not os.access(DATA_ROOT, os.W_OK):
        raise RuntimeError(
            f"THERMOFOOTY_DATA_ROOT = {DATA_ROOT} exists but is not "
            f"writeable by the current user."
        )
