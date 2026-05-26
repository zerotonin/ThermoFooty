# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — config                                            ║
# ║  « single source of truth for data-root + ingestion settings »   ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  All paths below derive from THERMOFOOTY_DATA_ROOT (env var) so  ║
# ║  the same codebase runs on a workstation NVMe, an HPC scratch    ║
# ║  mount, or a collaborator's laptop.  Default falls back to the   ║
# ║  in-repo ``data/`` symlink, which each developer points at       ║
# ║  whatever fast storage their box exposes.                        ║
# ║                                                                  ║
# ║  Mounting policy: import-time check ensures the data root        ║
# ║  exists and is writeable.  Loud failure beats silent fall-back   ║
# ║  to the wrong drive.                                             ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Data-root resolution + ingestion settings for ThermoFooty."""

from __future__ import annotations

import json
import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
#  Data-root resolution
# ─────────────────────────────────────────────────────────────────

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

#: Optional machine-local override file.  Gitignored so machine-specific
#: absolute paths never land in the public repo; ``local_paths.template.json``
#: is the committed scaffold a new developer copies and fills in.
LOCAL_PATHS_FILE: Path = REPO_ROOT / "local_paths.json"


def _read_local_data_root() -> str | None:
    """Return ``data_root`` from local_paths.json if the file exists and is valid."""
    if not LOCAL_PATHS_FILE.exists():
        return None
    try:
        payload = json.loads(LOCAL_PATHS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("data_root")
    return value if isinstance(value, str) and value.strip() else None


#: Resolution order (first hit wins):
#:   1. ``THERMOFOOTY_DATA_ROOT`` environment variable
#:   2. ``data_root`` from ``local_paths.json`` (gitignored, per-machine)
#:   3. In-repo ``data/`` symlink fallback
DATA_ROOT: Path = Path(
    os.environ.get("THERMOFOOTY_DATA_ROOT")
    or _read_local_data_root()
    or REPO_ROOT / "data"
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
    is missing — the typical cause is the external data volume not
    being mounted at boot.
    """
    if not DATA_ROOT.exists():
        raise RuntimeError(
            f"Resolved data root {DATA_ROOT} does not exist.  Either "
            f"set THERMOFOOTY_DATA_ROOT in the environment, or copy "
            f"local_paths.template.json -> local_paths.json and fill "
            f"in the data_root field.  Expected sub-directory layout "
            f"is documented in README.md -> Data layout."
        )
    if not os.access(DATA_ROOT, os.W_OK):
        raise RuntimeError(
            f"THERMOFOOTY_DATA_ROOT = {DATA_ROOT} exists but is not "
            f"writeable by the current user."
        )
