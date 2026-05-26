-- ╔══════════════════════════════════════════════════════════════════╗
-- ║  ThermoFooty — canonical SQLite schema                           ║
-- ║  « single source of truth for the analysis-ready data model »    ║
-- ╠══════════════════════════════════════════════════════════════════╣
-- ║  Backs the pre-registered analysis pipeline (OSF DOI            ║
-- ║  10.17605/OSF.IO/YZVAK).  Schema version tracked via the         ║
-- ║  `schema_version` table at the bottom; migrations under          ║
-- ║  `db/migrations/NNNN_<slug>.sql` are applied in numeric order    ║
-- ║  by `thermofooty.db.apply_migrations()`.                         ║
-- ║                                                                  ║
-- ║  Foreign keys MUST be enforced (`PRAGMA foreign_keys = ON`) on   ║
-- ║  every connection — the `cards` / `fouls` tables are foreign-    ║
-- ║  keyed to `lineups` and rely on FK enforcement to surface        ║
-- ║  orphaned card events at ingestion time rather than at fit       ║
-- ║  time.  `thermofooty.db.connect()` sets this pragma.             ║
-- ╚══════════════════════════════════════════════════════════════════╝

-- Per the pre-registration's § 3.6 design, the lineups table is the
-- structural backbone for H5 / H_break_player / H_mobility_* analyses.
-- Every player-level fit needs the (player × match) participation rows
-- INCLUDING uncarded matches — without the uncarded denominator the
-- per-player dose-response curve cannot be fit.

-- ─────────────────────────────────────────────────────────────
--  Core entities
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS countries (
    country_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    iso_alpha2   TEXT NOT NULL UNIQUE,          -- e.g. 'EN', 'DE', 'ES', 'IT', 'FR'
    name         TEXT NOT NULL                  -- e.g. 'England', 'Germany'
);

CREATE TABLE IF NOT EXISTS leagues (
    league_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    country_id       INTEGER NOT NULL REFERENCES countries(country_id),
    name             TEXT NOT NULL,             -- e.g. 'Premier League', '3. Liga'
    tier             INTEGER NOT NULL,          -- 1, 2, 3
    short_code       TEXT NOT NULL UNIQUE,      -- 'EN_PREM', 'EN_CHAMP', 'EN_L1', 'DE_BL1', …
    in_primary_panel INTEGER NOT NULL DEFAULT 1 -- 1 = in pre-registered primary panel; 0 = contingency
);

CREATE TABLE IF NOT EXISTS clubs (
    club_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    country_id   INTEGER NOT NULL REFERENCES countries(country_id),
    short_name   TEXT,                          -- canonical fbref / football-data short
    fbref_id     TEXT,                          -- fbref slug for joining
    UNIQUE (name, country_id)
);

CREATE TABLE IF NOT EXISTS stadia (
    stadium_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    country_id      INTEGER NOT NULL REFERENCES countries(country_id),
    city            TEXT,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    altitude_m      REAL,                        -- AGL; > 2000 m excluded per § 3.4
    has_roof        INTEGER NOT NULL DEFAULT 0,  -- 0 = open-air; 1 = retractable / fixed roof
    qatar2022_cooled INTEGER NOT NULL DEFAULT 0, -- 1 = one of 7 actively-cooled Qatar 2022 venues
    nearest_icao    TEXT,                        -- ICAO airport code for METAR lookup
    nearest_icao_distance_km REAL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS referees (
    referee_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    country_id      INTEGER REFERENCES countries(country_id),
    UNIQUE (name, country_id)
);

CREATE TABLE IF NOT EXISTS players (
    player_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    fbref_id        TEXT UNIQUE,                 -- fbref slug for joining
    birth_country_id INTEGER REFERENCES countries(country_id),
    birth_date      TEXT                         -- ISO-8601 yyyy-mm-dd
);

-- ─────────────────────────────────────────────────────────────
--  Tournament dimension  (for the tournament-panel hypotheses)
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tournaments (
    tournament_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    family          TEXT NOT NULL,               -- 'FIFA World Cup', 'UEFA Euro', 'Copa America', …
    short_code      TEXT NOT NULL UNIQUE         -- 'WC', 'EURO', 'COPA', 'AFCON', 'AFC', 'GOLD', 'CONFED'
);

CREATE TABLE IF NOT EXISTS tournament_editions (
    edition_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id    INTEGER NOT NULL REFERENCES tournaments(tournament_id),
    year             INTEGER NOT NULL,
    host_country     TEXT,                       -- free text; multi-host tournaments use 'USA / MEX / CAN'
    short_code       TEXT NOT NULL UNIQUE,       -- 'WC_2022', 'EURO_2024', 'COPA_1986', …
    qatar2022_flag   INTEGER NOT NULL DEFAULT 0  -- 1 only for WC_2022; carries the H7c / H6b special case
);

-- ─────────────────────────────────────────────────────────────
--  Match (the analytical event row)
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS matches (
    match_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id         INTEGER REFERENCES leagues(league_id),
    edition_id        INTEGER REFERENCES tournament_editions(edition_id),
    -- (XOR constraint on league_id / edition_id: see end-of-table CHECK below)
    season            TEXT,                       -- e.g. '2023-24'
    match_date        TEXT NOT NULL,              -- ISO yyyy-mm-dd
    kickoff_time      TEXT,                       -- HH:MM (local stadium time); NULL if unknown
    home_club_id      INTEGER NOT NULL REFERENCES clubs(club_id),
    away_club_id      INTEGER NOT NULL REFERENCES clubs(club_id),
    stadium_id        INTEGER NOT NULL REFERENCES stadia(stadium_id),
    referee_id        INTEGER REFERENCES referees(referee_id),
    attendance        INTEGER,                    -- NULL where missing; missingness flag downstream
    ft_home_goals     INTEGER,
    ft_away_goals     INTEGER,
    ht_home_goals     INTEGER,
    ht_away_goals     INTEGER,
    -- Match-level card aggregates from football-data.co.uk (HY+HR, AY+AR).
    -- NULL when the source season pre-dates per-side card columns.  Phase-3
    -- fbref ingestion supersedes these with per-player card events in the
    -- `cards` table; the aggregates here remain as a fast-aggregation
    -- denominator and a cross-source sanity check.
    card_count_home   INTEGER,
    card_count_away   INTEGER,
    roof_closed       INTEGER,                    -- 0/1/NULL — only meaningful for retractable-roof venues
    high_stakes_flag  INTEGER NOT NULL DEFAULT 0, -- per § 2 H4 definition; computed at ingestion
    data_tier         TEXT NOT NULL,              -- 'A' = post-1995 minute-level; 'B' = match-level only
    -- football-data.co.uk / fbref source provenance for the match row itself:
    source_primary    TEXT NOT NULL,              -- 'football_data_uk' | 'fbref' | 'wikipedia' | 'rsssf' | …
    source_url        TEXT,
    UNIQUE (stadium_id, match_date, home_club_id, away_club_id),
    -- Exactly one of league_id or edition_id must be non-null (XOR).
    -- Table-level CHECK constraints in SQLite must follow all column
    -- definitions; placed here rather than inline with the columns
    -- for that reason.
    CHECK (
        (league_id IS NOT NULL AND edition_id IS NULL)
        OR (league_id IS NULL AND edition_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_matches_date     ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_stadium  ON matches(stadium_id, match_date);
CREATE INDEX IF NOT EXISTS idx_matches_league   ON matches(league_id, season);
CREATE INDEX IF NOT EXISTS idx_matches_edition  ON matches(edition_id);
CREATE INDEX IF NOT EXISTS idx_matches_referee  ON matches(referee_id);

-- ─────────────────────────────────────────────────────────────
--  Lineups  « the STRUCTURAL BACKBONE for player-level fits »
-- ─────────────────────────────────────────────────────────────
-- One row per (player, match) participation.  Both carded AND
-- uncarded matches must be present — the per-player dose-response
-- and within-player conditional logit require the uncarded
-- denominator and CANNOT be fit on card-event records alone.
-- See § 3.6 of the pre-registration for the design rationale.

CREATE TABLE IF NOT EXISTS lineups (
    lineup_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL REFERENCES matches(match_id),
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    club_id         INTEGER NOT NULL REFERENCES clubs(club_id),    -- team the player suited up for
    is_home         INTEGER NOT NULL,              -- 0/1
    started         INTEGER NOT NULL DEFAULT 1,    -- 1 = in starting XI; 0 = substitute appearance only
    minutes_played  INTEGER,                       -- NULL if unknown
    position        TEXT,                          -- 'GK', 'DF', 'MF', 'FW' (or fbref fine-grained)
    UNIQUE (match_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_lineups_match    ON lineups(match_id);
CREATE INDEX IF NOT EXISTS idx_lineups_player   ON lineups(player_id);
CREATE INDEX IF NOT EXISTS idx_lineups_player_match ON lineups(player_id, match_id);

-- ─────────────────────────────────────────────────────────────
--  Outcome event tables  « FK to lineups, not just matches »
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cards (
    card_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lineup_id        INTEGER NOT NULL REFERENCES lineups(lineup_id),  -- FK to (player × match)
    match_id         INTEGER NOT NULL REFERENCES matches(match_id),   -- denormalised for fast match-level aggregations
    minute_of_issue  INTEGER,                       -- 0–90+; NULL if pre-minute-data era
    card_color       TEXT NOT NULL,                 -- 'yellow' | 'red' | 'second_yellow_red'
    card_reason      TEXT,                          -- free-text from fbref; parsed into category below
    aggression_set   INTEGER NOT NULL DEFAULT 0,    -- 1 = violent conduct / serious foul play /
                                                    -- spitting / abusive conduct toward officials
                                                    -- (the H0_spec aggression set, per § 2)
    source           TEXT NOT NULL                  -- 'fbref' | 'football_data_uk' | 'manual' | …
);

CREATE INDEX IF NOT EXISTS idx_cards_match      ON cards(match_id);
CREATE INDEX IF NOT EXISTS idx_cards_lineup     ON cards(lineup_id);
CREATE INDEX IF NOT EXISTS idx_cards_aggression ON cards(aggression_set);

CREATE TABLE IF NOT EXISTS fouls (
    foul_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id         INTEGER NOT NULL REFERENCES matches(match_id),
    lineup_id        INTEGER REFERENCES lineups(lineup_id),          -- nullable: fouls aren't always per-player
    fouls_count      INTEGER NOT NULL,
    source           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fouls_match ON fouls(match_id);

CREATE TABLE IF NOT EXISTS arrests (
    arrest_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id         INTEGER REFERENCES matches(match_id),           -- nullable for club-season aggregates
    club_id          INTEGER REFERENCES clubs(club_id),              -- only used when match_id is null
    season           TEXT,                                            -- '2018-19' etc; used with club_id for aggregates
    country_id       INTEGER NOT NULL REFERENCES countries(country_id),
    n_arrests        INTEGER NOT NULL,
    offence_category TEXT,                                            -- 'violent_disorder' | 'public_order' | …
    is_match_level   INTEGER NOT NULL,                                -- 1 if per-match; 0 if club-season aggregate
    source           TEXT NOT NULL                                    -- 'home_office' | 'zis_jahresberichte' | …
);

CREATE INDEX IF NOT EXISTS idx_arrests_match ON arrests(match_id);
CREATE INDEX IF NOT EXISTS idx_arrests_club_season ON arrests(club_id, season);

-- ─────────────────────────────────────────────────────────────
--  Weather  « one row per (stadium, date) — joined to matches »
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS weather (
    weather_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stadium_id           INTEGER NOT NULL REFERENCES stadia(stadium_id),
    observation_date     TEXT NOT NULL,             -- ISO yyyy-mm-dd
    tmax_obs_c           REAL,                       -- observed daily max (NULL = no value)
    tmax_anomaly_c       REAL,                       -- = tmax_obs_c − baseline_mean_c (NULL if either NULL)
    baseline_mean_c      REAL,                       -- ±5-yr same-month-same-stadium mean
    baseline_std_c       REAL,                       -- for sigma rescaling
    baseline_n_days      INTEGER,                    -- sample size of the baseline window
    source_tier          TEXT NOT NULL,              -- 'tier1_ghcn' | 'tier2_hadcet_max' |
                                                     -- 'tier2_hadcet_mean' | 'tier3_era5' |
                                                     -- 'tier4_20crv3' | 'unverifiable'
    source_id            TEXT,                       -- station code / grid cell id
    note                 TEXT,
    UNIQUE (stadium_id, observation_date)
);

CREATE INDEX IF NOT EXISTS idx_weather_stadium_date ON weather(stadium_id, observation_date);
CREATE INDEX IF NOT EXISTS idx_weather_source_tier  ON weather(source_tier);

-- ─────────────────────────────────────────────────────────────
--  Provenance  « ingestion-run audit trail »
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS data_provenance (
    provenance_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT NOT NULL,                  -- 'football_data_uk' | 'fbref' | 'home_office' | 'zis' | 'meteostat' | 'era5' | …
    accessed_at      TEXT NOT NULL,                  -- ISO-8601 yyyy-mm-ddThh:mm:ssZ
    n_rows_pulled    INTEGER,
    sha256_payload   TEXT,                           -- SHA-256 of the fetched bytes (for reproducibility audit)
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_provenance_source ON data_provenance(source, accessed_at);

-- ─────────────────────────────────────────────────────────────
--  Schema versioning  « head pointer for alembic-lite »
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema_version (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table
    current_head    INTEGER NOT NULL,                     -- highest migration applied
    applied_at      TEXT NOT NULL                         -- ISO-8601
);

-- Seed the schema version to 1 (this file represents migration 0001).
-- Migrations directory (`db/migrations/NNNN_<slug>.sql`) builds on top.
INSERT OR IGNORE INTO schema_version (id, current_head, applied_at)
VALUES (1, 1, datetime('now'));
