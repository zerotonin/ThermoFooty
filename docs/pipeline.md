# Pipeline overview (planned)

ThermoFooty is a staged pipeline. As of v0.1.0-dev0 the repo is a
scaffold — only Phase 1 (this document, the SQLite schema, the
package skeleton) is implemented. Subsequent phases land per the
dev plan at `~/PyProjects/ThermoFooty_DEV_PLAN.md`.

## Stage 1 — Match data ingestion

- **football-data.co.uk** for Big-5 tier 1 + 2 + EFL League One
  match results (score, cards per side, attendance, referee)
- Per-source ingester writes into `matches` + `cards` + (where
  available) `fouls` tables of the SQLite database

## Stage 2 — Lineup + per-player card ingestion (fbref)

- **fbref.com** via worldfootballR subprocess for:
  - Per-match lineup tables (every player who started or was
    substituted in — both carded and uncarded matches)
  - Per-card minute-of-issue + card reason
- Joins onto `lineups` and `cards` tables
- **Critical:** uncarded matches must be present in the `lineups`
  table — the per-player dose-response analyses (H_break_player,
  H_mobility_*) need the uncarded denominator and cannot be fit on
  card-event records alone

## Stage 3 — Crowd-violence arrests ingestion

- **UK Home Office** football-related arrests bulletins (PDF parse)
  for English football tiers 1–3, 1984–2026
- **Bundespolizei ZIS-Jahresberichte** for German football tiers 1–3,
  2003–2026
- Pooled in the `arrests` table with a country fixed effect for the
  H2 / H4b analyses

## Stage 4 — Stadium-day weather backfill

- Four-tier cascade vendored from
  [ThermoStrife v0.1.1](https://doi.org/10.5281/zenodo.20371612):
  - Tier 1 — METAR via meteostat 2.x
  - Tier 2 — HadCET (British Isles only)
  - Tier 3 — ECMWF ERA5 reanalysis (1981+)
  - Tier 4 — NOAA 20CRv3 reanalysis (1806–1980, only used for
    pre-1981 tournament matches)
- Per-(stadium, date) results land in the `weather` table with
  `tmax_obs_c`, `tmax_anomaly_c`, `baseline_mean_c`,
  `baseline_std_c`, `baseline_n_days`, `source_tier`

## Stage 5 — Analysis-panel materialisation

- LEFT JOIN of `matches × lineups × cards × weather × ...` produces
  `analysis_panel.parquet` under
  `$THERMOFOOTY_DATA_ROOT/derived/`
- One materialisation per ingestion pass; checksum captured in
  `data_provenance`

## Stage 6 — Hypothesis fits

- H1 (primary) via `thermofooty.inference.run_h1()` →
  `rerandomstats.case_crossover_conditional_logit`
- League auxiliary battery (H2 / H3 / H4 / H4b / H5 / H0_spec /
  H_league_het)
- Dose-response battery (H_break_pop / H_break_player /
  H_mobility_transfer / H_mobility_dual)
- Tournament battery (H6 / H6b / H7 / H7c / H8 / H_omnibus)
- All BH-FDR + Bonferroni correction routed through
  `rerandomstats.benjamini_hochberg`

## Stage 7 — SOTA viz

- Raincloud + null density + forest plot + superposed epoch +
  warming-stripes timeline. Triple-output SVG + PNG + CSV per the
  lab convention.
