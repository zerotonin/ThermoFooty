# ThermoFooty

[![tests](https://github.com/zerotonin/ThermoFooty/actions/workflows/tests.yml/badge.svg)](https://github.com/zerotonin/ThermoFooty/actions/workflows/tests.yml)
[![docs](https://github.com/zerotonin/ThermoFooty/actions/workflows/docs.yml/badge.svg)](https://zerotonin.github.io/ThermoFooty/)
[![release](https://github.com/zerotonin/ThermoFooty/actions/workflows/release.yml/badge.svg)](https://github.com/zerotonin/ThermoFooty/releases)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.PENDING.svg)](https://zenodo.org/)
[![Pre-registration: OSF](https://img.shields.io/badge/Pre--reg-OSF%2010.17605%2FOSF.IO%2FYZVAK-0072B2)](https://doi.org/10.17605/OSF.IO/YZVAK)
[![AsPredicted](https://img.shields.io/badge/AsPredicted-H1%20one--pager-009E73)](https://aspredicted.org/av2un9.pdf)
[![Companion: ThermoStrife](https://img.shields.io/badge/companion-ThermoStrife-CC79A7.svg)](https://doi.org/10.5281/zenodo.20371612)
[![Companion: reRandomStats](https://img.shields.io/badge/stats-reRandomStats%20v0.2.0-E69F00.svg)](https://doi.org/10.5281/zenodo.20387255)

> **Status:** scaffold (v0.1.0-dev0). Pre-registration is locked at
> OSF; data ingestion and analysis pipelines are under construction
> per the dev plan at `~/PyProjects/ThermoFooty_DEV_PLAN.md`. The
> Zenodo DOI badge above will be minted at the first tagged release.

**Does on-pitch player aggression rise with the day-of-match
temperature anomaly at the stadium, and does the same heat signal
extend from the pitch to the supporters?** ThermoFooty pre-registers
and executes a natural-experiment test of the heat-aggression
hypothesis on European soccer.

The design exploits the fact that **fixtures are scheduled before
weather is realised**, eliminating the Field-1992 outdoor-opportunity
confound that limits modern crime-data designs. The same scheduled-
fixture identification underlies every analysis in the project, from
the Big-5 European league panel (H1: ~150 000+ matches) to the
tournament panel (H6/H6b on Qatar 2022 Stadium 974 as a within-
tournament natural-control on cooled vs naturally-ventilated venues).

The full pre-registered design — primary confirmatory test plus 17
auxiliary hypotheses across three independently BH-FDR-corrected
batteries — is locked at OSF
([10.17605/OSF.IO/YZVAK](https://doi.org/10.17605/OSF.IO/YZVAK)) with
an AsPredicted one-pager cross-post for the H1 confirmatory test
([aspredicted.org/av2un9.pdf](https://aspredicted.org/av2un9.pdf)).

## Position in the lab's heat-aggression programme

ThermoFooty is one chapter of a three-track cross-species programme:

- **[ThermoKourt](https://github.com/zerotonin/thermokourt)** —
  *Drosophila* track. Behavioural-arena heat-aggression assays under
  controlled thermal manipulation.
- **[ThermoStrife](https://github.com/zerotonin/thermostrife)**
  ([Zenodo DOI 10.5281/zenodo.20371612](https://doi.org/10.5281/zenodo.20371612)) —
  human-data track, historical-uprisings analysis. 112-event
  case-crossover panel 1750–2024 with four-tier weather backfill;
  headline OR = 1.089 per +1 °C above local same-month baseline.
- **ThermoFooty** (this repo) — human-data track, soccer panel.
  Pre-registered natural-experiment test on scheduled fixtures
  1970–2026, addressing the small-n + selection-bias critiques of
  the ThermoStrife historical panel.

The three tracks publish separately but share the conceptual
hypothesis and (where appropriate) code: the four-tier weather
cascade ThermoFooty uses is vendored verbatim from ThermoStrife
v0.1.1, and every statistical estimator routes through
[reRandomStats v0.2.0+](https://doi.org/10.5281/zenodo.20387255)
([case_crossover](https://github.com/zerotonin/reRandomStats/blob/main/rerandomstats/case_crossover.py),
[model_comparison](https://github.com/zerotonin/reRandomStats/blob/main/rerandomstats/model_comparison.py),
[dose_response](https://github.com/zerotonin/reRandomStats/blob/main/rerandomstats/dose_response.py)).

## Pre-registered hypotheses (summary)

| Battery | Hypothesis | Quick description |
|---|---|---|
| **PRIMARY** | H1 | Per-match red-card-for-violent-conduct odds rise with stadium-day Tmax anomaly. Time-stratified case-crossover conditional logit on Big-5 1970–2026. **Single confirmatory test, uncorrected α = 0.05, one-sided.** |
| LEAGUE auxiliary (7 tests, BH FDR q = 0.05) | H2 | Crowd-violence arrests (pooled UK Home Office + ZIS-Jahresberichte) rise with the same anomaly exposure. |
| | H3 | Heat coefficient attenuated in closed-roof / cooled stadia. |
| | H4 / H4b | Heat × stakes interaction on player cards / crowd arrests. |
| | H5 | Within-player FE: same player carded more in hot matches. |
| | H0_spec | Aggression-set cards rise faster than non-card fouls (mechanism specificity). |
| | H_league_het | LRT for cross-league slope heterogeneity. |
| DOSE-RESPONSE (4 tests, BH FDR q = 0.05) | H_break_pop / H_break_player | Segmented regression + Davies test + 4PL Hill rescue; population and per-player breakpoints. |
| | H_mobility_transfer / H_mobility_dual | Player-transfer natural experiment on absolute-vs-anomaly exposure. |
| TOURNAMENT (6 tests, BH FDR q = 0.05) | H6 | Cooled-stadia attenuation in pooled tournament panel. |
| | H6b | Qatar 2022 Stadium 974 (naturally ventilated, n=7) vs the seven cooled venues (n=57). |
| | H7 / H7c | Hot-vs-cool host World Cups (Qatar excluded; Qatar as own descriptive category). |
| | H8 / H_omnibus | Tournament-family / tournament-edition heterogeneity LRTs. |

Full specifications in the OSF pre-registration and in the lab's
internal markdown at
`~/ObsidianVault/GeurtenLab/Projects/HeatAggressionDrosophila/human_aggression/preregistration_thermofooty.md`.

## Repository layout

```
ThermoFooty/
├── pyproject.toml
├── CITATION.cff
├── environment.yml
├── LICENSE                          ← MIT
├── README.md
├── data → /media/geuba03p/DATADRIVE1/ThermoFooty   ← symlink (gitignored)
├── db/
│   ├── schema.sql                   ← committed canonical DDL
│   └── migrations/                  ← alembic-lite NNNN_<slug>.sql
├── thermofooty/                     ← Python package
│   ├── __init__.py
│   ├── constants.py                 ← Wong palette, paths, type aliases
│   ├── config.py                    ← THERMOFOOTY_DATA_ROOT env var
│   ├── db/                          ← SQLite session, schema-version check
│   ├── sources/                     ← football_data_uk, fbref, home_office, zis
│   ├── weather/                     ← vendored cascade from ThermoStrife v0.1.1
│   ├── lookup.py                    ← (stadium, date) → AnomalyFetch
│   ├── panel.py                     ← analysis_panel materialiser
│   ├── inference.py                 ← thin wrapper around reRandomStats
│   └── viz.py                       ← Wong-palette figures
├── scripts/                         ← ingestion + analysis CLI scripts
├── tests/
├── docs/                            ← Sphinx docs
└── .github/workflows/               ← tests + docs + release + network-tests
```

## Data layout (on DATADRIVE1)

All data lives off-repo on the NVMe at `/media/geuba03p/DATADRIVE1/ThermoFooty/`,
exposed via the gitignored `data/` symlink. Override with the
`THERMOFOOTY_DATA_ROOT` env var on a different machine.

```
$THERMOFOOTY_DATA_ROOT/
├── db/
│   └── thermofooty.sqlite           ← canonical SQLite (built from db/schema.sql)
├── raw/
│   ├── football_data_uk/            ← season-per-CSV downloads
│   ├── fbref_html/                  ← scraped match-report HTML cache
│   ├── home_office_pdfs/            ← UK arrests bulletins
│   ├── zis_jahresberichte/          ← Bundespolizei annual reports
│   ├── stadia/                      ← coordinate CSVs, lineup overrides
│   └── observatories/hadcet/        ← HadCET daily totals files
├── cache/
│   ├── meteostat/                   ← parquet per (station, year-month)
│   ├── era5/                        ← parquet per (cell, year-month)
│   ├── twentycr/                    ← parquet per (cell, year)
│   └── fbref_parsed/                ← parsed JSON per match (dedupe key)
├── derived/
│   └── analysis_panel.parquet       ← materialised join per ingestion pass
└── logs/                            ← ingestion + analysis logs
```

## Installation

```bash
git clone https://github.com/zerotonin/ThermoFooty.git
cd ThermoFooty
pip install -e ".[all]"

# Re-point `data/` at your data root (defaults to DATADRIVE1; override if needed).
export THERMOFOOTY_DATA_ROOT=/media/geuba03p/DATADRIVE1/ThermoFooty
ln -sf "$THERMOFOOTY_DATA_ROOT" data
```

Python ≥ 3.11 required (meteostat 2.x dropped 3.10). For the ERA5
fallback tier you additionally need a free
[Copernicus CDS API key](https://cds.climate.copernicus.eu/api-how-to)
in `~/.cdsapirc` (gitignored).

## Citation

If you use ThermoFooty in published work, please cite **both** the
software (version DOI to appear on first GitHub Release) and the
underlying OSF pre-registration:

> Geurten, B. R. H. (2026). *ThermoFooty: heat as an acute trigger
> of on-pitch aggression — pre-registered natural-experiment test on
> European soccer*. OSF. https://doi.org/10.17605/OSF.IO/YZVAK

Full citation metadata in `CITATION.cff`. Companion citations for
[ThermoStrife](https://doi.org/10.5281/zenodo.20371612) and
[reRandomStats](https://doi.org/10.5281/zenodo.20387255) are listed
in the same file under `references`.

## Authors

Bart R. H. Geurten — Department of Zoology, University of Otago,
Dunedin, New Zealand. ORCID
[0000-0002-1816-3241](https://orcid.org/0000-0002-1816-3241).

## License

MIT — see `LICENSE`.
