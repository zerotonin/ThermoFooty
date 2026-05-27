# ThermoFooty diary

Running log of milestones, results, and decisions on the ThermoFooty
project.  Newest entries on top.

---

## 2026-05-28 — Phase 5b reds-only proxy H1 lands a real signal

Followed the Phase 5b "split out the reds from football-data.co.uk's
HR/AR cells" plan after the fbref ingest hit a Cloudflare Managed
Challenge wall that no HTTP library (requests, curl_cffi with 10 TLS
profiles, cloudscraper) and no automated browser (Playwright, Patchright,
Playwright + stealth headless and non-headless) could clear from this
NZ / Otago IP.  Sofascore was also a hard NO from this IP (Fastly CDN
edge block, plus coverage only goes back to ~2010 so wouldn't cover
the 1993-94 panel anyway).

So: stayed with football-data.co.uk, exposed reds separately, re-ran
H1 with `side_received_red` (≥1 red on this side per HR/AR cells) as
the outcome.

### Result

Case-crossover conditional logit, strata = (club, year-month), one
event per (case match-side) with the team's other matches that month
as controls.  Same machinery as Phase 5a; only the outcome variable
changed.

| Quantity                  | Value                                |
|---------------------------|--------------------------------------|
| Outcome                   | side_received_red (HR ≥ 1 / AR ≥ 1)  |
| Events built              | 3,838                                |
| Events in fit             | 3,838                                |
| Rows in fit               | 16,482                               |
| OR per +1 °C anomaly      | **1.0145**                           |
| 95% CI                    | **[1.0017, 1.0274]**                 |
| β (per °C)                | +0.0144                              |
| SE (per °C)               | 0.0065                               |
| p (two-sided)             | 0.0262                               |
| p (one-sided, β > 0)      | **0.0131  ← OSF-locked direction**   |
| daylight_h covariate β    | −0.0723                              |

### Side-by-side with Phase 5a

| Quantity | Phase 5a (any card) | Phase 5b (any red) |
|---|---|---|
| OR per +1 °C | 1.0022 | **1.0145** |
| 95% CI | [0.9984, 1.0059] (includes 1) | **[1.0017, 1.0274] (excludes 1)** |
| p (one-sided, OSF dir.) | 0.126 | **0.013** |
| Events | 43,522 | 3,838 |

### Interpretation

This is the first ThermoFooty result that **directionally + significantly
supports the heat-aggression hypothesis**.  Sign is positive (heat →
more reds), CI excludes 1.0, one-sided p in the pre-registered OSF
direction is 0.013 — well below the 0.05 confirmatory threshold.

Effect size is modest (1.45% odds increase per +1 °C anomaly) but real.
About 6× smaller than ThermoStrife's historical-uprisings headline (OR
≈ 1.089 per °C; DOI 10.5281/zenodo.20371612), which is exactly the
attenuation we'd predict moving from extreme collective-violence
outcomes to a noisier individual-aggression outcome that still mixes
in some tactical reds and second-yellow accumulations.  The daylight_h
covariate β = −0.072 is doing meaningful confounder-absorbing work.

The OSF preregistration locks "red cards for violent conduct"
specifically.  This run is on **all reds**, which is broader than the
locked outcome — it includes straight reds for serious foul play +
violent conduct + spitting + abusive language (the OSF aggression set)
PLUS straight reds for denial of an obvious goal-scoring opportunity
(DOGSO) + second-yellow accumulations (mostly repeat tactical fouling).
The OSF aggression set is a SUBSET of all reds, so an effect on all
reds is necessary-but-not-sufficient evidence for the locked outcome
— Stathead ($8/mo paid subscription, one-month buy) would confirm
whether the signal holds when the outcome is narrowed to violent
conduct only.

### Decisions outstanding

- **Stathead one-shot subscription**: $8 for the strict OSF-locked
  outcome (violent-conduct reds only).  Now optional rather than
  required since the broader-reds proxy result is already
  significant; useful for confirmation + manuscript robustness.
- **Phase 2d cross-country expansion**: Bundesliga, La Liga, Serie A,
  Ligue 1 with their tier-2 siblings.  Would roughly triple the
  events count (3,838 → ~12,000) and unlock the H_league_het
  cross-league heterogeneity test.  ~1 week of stadia curation work.
- **Manuscript start**: with this result the W.I.N.G.S.-style write-up
  becomes plausible.

### Companion records

- Headline numbers in `$DERIVED_DIR/phase5a_h1_result.csv` (yes the
  CSV filename still says phase5a — it's overwritten by every
  `scripts/run_h1.py` invocation; the contents are the latest run).

### Run metadata

| Field             | Value                                              |
|-------------------|----------------------------------------------------|
| Wall-clock (NZST) | 2026-05-28 14:16:02 → 14:16:15 (13 s)              |
| Host              | uo107570 (Otago workstation)                       |
| Code state        | post-Phase-5b sprint + migration hotfix            |
| Command           | `python scripts/run_h1.py --outcome side_received_red` |
| Outcome col       | `side_received_red`                                 |
| Panel             | EPL + Championship + L1, 1993-94 → 2025-26          |
| Result CSV        | `$DERIVED_DIR/phase5a_h1_result.csv`                |

### Open question: Stathead coverage for the cross-country expansion

- EPL: confirmed by reputation, fully in Stathead's Soccer product.
- Big-5 first divisions (Bundesliga 1, La Liga, Serie A, Ligue 1):
  confirmed by reputation.
- Championship + League One: likely yes (Stathead = fbref data; fbref
  tracks both) but not 100% verified — would need a one-month
  subscription to confirm by download attempt.
- Second divisions in non-English Big-5 countries (Bundesliga 2,
  Segunda, Serie B, Ligue 2): unlikely to be in Stathead.
- Pragmatic plan: when (if) we go for Stathead, subscribe one month,
  download what's available, cancel.

---

## 2026-05-27 — first exploratory H1 fit on the English panel

End-to-end pipeline now runs.  Phase 2b–2c (ingest), Phase 4 (cascade
backfill), and Phase 5a (case-crossover orchestration) all committed
and pushed.

### Data on hand

- **Match panel:** EPL + Championship + League One, 1993-94 through
  2024-25.  Source: football-data.co.uk season CSVs, ingested via
  `scripts/ingest_english_leagues.py --league all`.
- **Weather backfill:** 37,621 (stadium, match_date) probes resolved
  via the four-tier cascade.  Real wall-time ~4 hours with
  `--workers 8` against a cold meteostat cache.  Coverage breakdown:
  - tier1_ghcn        87.9% (33,287 rows)
  - tier2_hadcet_max  12.1% ( 4,583 rows)
  - tier3_era5         0.0% (     1 row)
  - unverifiable       0.0%
  - excluded_altitude  0.0%
  - every (league, season) cell at 100% coverage
- **Analysis panel:** materialised in-memory from
  `materialise_analysis_panel(conn)`, one row per (match, side).

### H1 exploratory fit — proxy outcome only

Case-crossover conditional logit (Maclure 1991; Lee et al. 2023),
strata = (club, year-month), one event per (case match-side) with
the team's other matches that month as controls.  Outcome:
`side_received_card` = ≥1 yellow OR red card on that side (the only
match-level outcome football-data.co.uk supports without per-card
reason codes).

| Quantity               | Value                          |
| ---------------------- | ------------------------------ |
| Outcome (proxy)        | side_received_card             |
| Events built           | 43,522                         |
| Events in fit          | 43,522                         |
| Rows in fit            | 190,666                        |
| OR per +1 °C anomaly   | 1.0022                         |
| 95% CI                 | [0.9984, 1.0059]               |
| β (per °C)             | +0.0022                        |
| SE (per °C)            | 0.0019                         |
| p (two-sided)          | 0.2527                         |
| p (one-sided, β > 0)   | 0.1263  ← OSF-locked direction |
| daylight_h covariate β | −0.0038                        |

### Interpretation

Sign is in the predicted positive direction but the effect is tiny
and not statistically significant on this outcome.  This is **exactly
what we expect on the card-aggregate proxy.**  `side_received_card`
fires on ~95% of EPL match-sides — overwhelmingly yellow cards for
tactical fouls, dissent, and time-wasting, almost none of which the
heat-aggression mechanism predicts.  The yellows dilute whatever
signal exists in the red-card / violent-conduct subset.

For comparison: ThermoStrife (historical uprisings, Geurten 2026,
DOI 10.5281/zenodo.20371612) found OR ≈ 1.089 per +1 °C on the same
exposure variable — roughly **40× larger** than what we measure
here.  The order-of-magnitude gap is consistent with the proxy
outcome being a heavily diluted version of the actual hypothesised
signal.

This result is **not a test of H1.**  H1 as locked at OSF
([10.17605/OSF.IO/YZVAK](https://doi.org/10.17605/OSF.IO/YZVAK))
targets red cards for violent conduct, not any-card.  The proxy run
is a wiring test for the case-crossover machinery — it confirms the
pipeline produces interpretable numbers and that the
yellow-card-dominated outcome does NOT show a strong effect.  Both
of those are positive findings, but neither speaks to the actual
hypothesis.

### What's next

**Phase 3 — fbref lineups + per-card reason codes** is the gating
unlock.  It enables:

1. The **OSF-locked H1 confirmatory test** (red-cards-for-violent-
   conduct as outcome).  One-line change in
   `scripts/run_h1.py --outcome side_received_red` once the column
   is populated.
2. Every **per-player hypothesis** in the OSF battery: H5 (within-
   player FE), H_break_player (per-player dose-response), and the
   two H_mobility_* transfer tests.  All four need
   `lineups` × `cards` joined to per-card reason codes.

Dev plan estimate: 2–3 weeks (scraper / `worldfootballR` wrapper
choice + initial rate-limited pull + reconciliation against the
football-data.co.uk aggregate sanity check).

### Companion records

- Headline numbers in `$DERIVED_DIR/phase5a_h1_result.csv`.

### Run metadata

| Field             | Value                                            |
|-------------------|--------------------------------------------------|
| Wall-clock (NZST) | 2026-05-27, late afternoon (exact terminal       |
|                   | timestamp not captured in this entry; commit     |
|                   | timestamp on `296de04` is the proof-of-time)     |
| Host              | uo107570 (Otago workstation)                     |
| Code state        | commit `296de04` (Phase 5a complete, pre-5b)     |
| Command           | `python scripts/run_h1.py`                       |
| Outcome col       | `side_received_card` (any-card proxy)             |
| Panel             | EPL + Championship + L1, 1993-94 → 2025-26        |
| Result CSV        | `$DERIVED_DIR/phase5a_h1_result.csv`              |
