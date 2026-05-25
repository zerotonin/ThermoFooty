"""Weather-cascade adapters (vendored from ThermoStrife v0.1.1).

Phase 1 (scaffold): module exists; the four-tier cascade
(``meteostat_src`` → ``hadcet_src`` → ``era5_src`` → ``twentycr_src``)
will be vendored verbatim from ThermoStrife v0.1.1 in Phase 2 of the
dev plan.  Each vendored adapter carries a top-of-file header noting
the sync source and date so it remains traceable.

The cascade was developed and validated on the ThermoStrife
historical-uprisings panel (Geurten 2026, DOI 10.5281/zenodo.20371612)
on a non-overlapping panel of events.  Cache paths re-root via
``thermofooty.config.CACHE_*`` so caches land on DATADRIVE1.
"""
