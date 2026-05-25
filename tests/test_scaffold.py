"""Phase-1 scaffold smoke tests.

These tests don't exercise any of the (yet-to-be-written) ingestion
or inference logic — they just verify that the scaffold itself is
internally consistent: the package imports cleanly, constants are
the values the pre-registration locked, the data root is reachable,
and the DDL file parses + applies without error against an
in-memory SQLite database.

When the network-touching adapters land in Phase 2+, separate test
files (test_ingest_*.py, test_lookup.py, etc.) will pick up
network-marked integration coverage; this file stays pure and fast.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import thermofooty
from thermofooty import config, constants

# ─────────────────────────────────────────────────────────────────
#  Package + version
# ─────────────────────────────────────────────────────────────────


def test_package_imports():
    assert thermofooty.__version__.startswith("0.")


def test_subpackages_import():
    """Every documented subpackage must be importable from a fresh shell."""
    import thermofooty.config  # noqa: F401
    import thermofooty.constants  # noqa: F401
    import thermofooty.db  # noqa: F401
    import thermofooty.inference  # noqa: F401
    import thermofooty.lookup  # noqa: F401
    import thermofooty.panel  # noqa: F401
    import thermofooty.sources  # noqa: F401
    import thermofooty.viz  # noqa: F401
    import thermofooty.weather  # noqa: F401


def test_inference_eagerly_imports_rerandomstats():
    """thermofooty.inference must surface the rerandomstats v0.2.0+
    symbols its docstring promises to delegate to.  If rerandomstats
    is missing or the wrong version, the failure must be at import
    time (not several minutes into an analysis run).
    """
    from thermofooty import inference
    assert hasattr(inference, "case_crossover_conditional_logit")
    assert hasattr(inference, "wald_two_sample_beta")
    assert hasattr(inference, "broken_stick_fit")
    assert hasattr(inference, "benjamini_hochberg")


# ─────────────────────────────────────────────────────────────────
#  Constants  « locked by the OSF pre-registration »
# ─────────────────────────────────────────────────────────────────


def test_baseline_window_locked_to_pre_registration():
    """§ 3.4 of the pre-registration locks the ±5-year baseline window."""
    assert constants.BASELINE_HALF_WINDOW_YEARS == 5


def test_altitude_cap_locked_to_pre_registration():
    """§ 3.4 of the pre-registration locks the > 2000 m exclusion."""
    assert constants.ALTITUDE_CAP_M == 2000


def test_wong_palette_has_eight_colours():
    assert len(constants.WONG) == 8


def test_semantic_colours_reference_palette():
    """Every semantic colour must come from the Wong palette (no ad-hoc hex)."""
    for label, hex_val in constants.SEMANTIC_COLOURS.items():
        assert hex_val in constants.WONG.values(), (
            f"{label} = {hex_val} is not in the Wong palette"
        )


# ─────────────────────────────────────────────────────────────────
#  Data-root resolution
# ─────────────────────────────────────────────────────────────────


def test_repo_root_contains_pyproject():
    assert (config.REPO_ROOT / "pyproject.toml").exists()


def test_data_root_resolves():
    """DATA_ROOT must resolve to something — either DATADRIVE1 or
    the in-repo `data/` symlink fallback.  Don't assert it exists
    here because CI runners won't have DATADRIVE1; that check
    belongs in assert_data_root_ready() called by the CLI scripts.
    """
    assert config.DATA_ROOT is not None
    assert isinstance(config.DATA_ROOT, Path)


def test_schema_sql_path_resolves_in_repo():
    assert config.SCHEMA_SQL_PATH.exists(), (
        f"db/schema.sql not found at {config.SCHEMA_SQL_PATH}"
    )


# ─────────────────────────────────────────────────────────────────
#  DDL parses + applies against an in-memory SQLite
# ─────────────────────────────────────────────────────────────────


def test_schema_ddl_applies_to_in_memory_sqlite():
    """The committed DDL must execute cleanly against a fresh SQLite.

    Catches syntax errors, missing semicolons, dangling FK references,
    and CHECK-constraint typos at every CI run.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        sql = config.SCHEMA_SQL_PATH.read_text()
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def test_lineups_table_is_per_player_per_match():
    """The pre-registration's § 3.6 requires the lineups table to be
    one row per (player × match) participation.  Check the UNIQUE
    constraint encodes that invariant so future schema edits don't
    accidentally drop it.
    """
    sql = config.SCHEMA_SQL_PATH.read_text().lower()
    assert "create table if not exists lineups" in sql
    assert "unique (match_id, player_id)" in sql


def test_pragma_foreign_keys_enforced():
    """Per § 3.6, every SQLite connection enables foreign-key
    enforcement.  thermofooty.db.connect() must set this pragma.
    """
    # We can't actually open the real DB on CI, but we CAN verify the
    # function body contains the pragma string.
    import inspect

    from thermofooty.db import connect
    src = inspect.getsource(connect)
    assert 'PRAGMA foreign_keys = ON' in src


# ─────────────────────────────────────────────────────────────────
#  Stub functions raise informative NotImplementedError
# ─────────────────────────────────────────────────────────────────


def test_lookup_stub_raises_with_phase_message():
    from thermofooty.lookup import resolve_event_anomaly
    with pytest.raises(NotImplementedError, match="Phase-2"):
        resolve_event_anomaly()


def test_panel_stub_raises_with_phase_message():
    from thermofooty.panel import materialise_analysis_panel
    with pytest.raises(NotImplementedError, match="Phase-4"):
        materialise_analysis_panel()


def test_inference_run_h1_stub_raises_with_phase_message():
    from thermofooty.inference import run_h1
    with pytest.raises(NotImplementedError, match="Phase-5"):
        run_h1()
