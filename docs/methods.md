# Methods

The locked pre-registered analysis is at OSF
([10.17605/OSF.IO/YZVAK](https://doi.org/10.17605/OSF.IO/YZVAK)) and
the H1 confirmatory one-pager is at AsPredicted
([aspredicted.org/av2un9.pdf](https://aspredicted.org/av2un9.pdf)).

This page is a short tour; the OSF pre-registration is the
authoritative document for every analytical choice.

## Identification — scheduled-fixture natural experiment

Football fixtures are scheduled weeks to months in advance.
Whatever weather arrives on a match day was determined by
atmospheric processes that pre-date and are independent of any
soccer-side decision (team selection, referee assignment, crowd
attendance). This eliminates the Field-1992 outdoor-opportunity
confound that limits incident-count crime-data designs, where
the rate of social contact itself is temperature-dependent.

## Primary estimator (H1)

Time-stratified case-crossover conditional logit
(Maclure 1991; Lee et al. 2023) on the Big-5 European league panel
1970–2026. Each match is one stratum; the case row is the
match-day observation; referent rows are all matches at the same
stadium, same calendar month, in years event_year ± 5, excluding
the event week ± 7 days. The conditional likelihood integrates
out the stratum intercepts, so every time-invariant stadium /
club / era characteristic is absorbed by construction.

Outcome: binary — "any direct red card for violent conduct in the
match." Exposure: stadium-day Tmax anomaly relative to the
stadium's same-calendar-month ±5-season local baseline.

## Estimator stack — all delegated to reRandomStats

Every confirmatory and auxiliary estimator routes through
[reRandomStats v0.2.0](https://doi.org/10.5281/zenodo.20387255):

- `case_crossover_conditional_logit` for H1, H3, H4
- `stratified_permutation` as the non-parametric backup
- Conditional-FE Poisson + `wald_two_sample_beta` for H2, H4b, H0_spec
- `broken_stick_fit` + `davies_test` + `pscore_test` + `hill_fit`
  for the dose-response battery (H_break_pop / H_break_player)
- `likelihood_ratio_test` for H8 / H_omnibus / H_league_het
- `benjamini_hochberg` for every multiple-comparisons family

The package's "single algorithmic source" invariant
([reRandomStats README](https://github.com/zerotonin/reRandomStats#features))
guarantees the underlying correction math has exactly one
implementation across the lab.

## Multiple comparisons

H1 is the **single confirmatory test**, reported at uncorrected
α = 0.05. The three auxiliary batteries are corrected
**independently** because they test different scientific questions
on different panels and pooling them into a single family would
over-correct:

| Battery | n_tests | Hypotheses |
|---|---:|---|
| League auxiliary | 7 | H2, H3, H4, H4b, H5, H0_spec, H_league_het |
| Dose-response | 4 | H_break_pop, H_break_player, H_mobility_transfer, H_mobility_dual |
| Tournament | 6 | H6, H6b, H7, H7c, H8, H_omnibus |

Benjamini–Hochberg FDR at q = 0.05 primary; Bonferroni-adjusted
p-values reported alongside as a conservative reference.

## What this design does NOT solve

The OSF pre-registration's § 1 enumerates four residual confounds
the design must explicitly handle (heat → physical-performance →
all fouls; heat → referee behaviour; heat → crowd intensity;
fixture-timing artefacts) and exactly which tests rule each out.
See § 1 of the attached pre-registration document.
