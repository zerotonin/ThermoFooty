"""Per-source data-ingestion adapters for ThermoFooty.

Phase 1 (scaffold): module exists; concrete adapters land in Phases
2–3 of the dev plan:

- ``football_data_uk`` — Big-5 tier-1 + tier-2 + tier-3 EFL League
  One match results (Phase 2)
- ``fbref`` — per-match lineups + per-player card events via
  worldfootballR subprocess (Phase 3)
- ``home_office`` — UK Home Office football-related arrests PDFs
  (Phase 2)
- ``zis`` — German Bundespolizei ZIS-Jahresberichte annual reports
  (Phase 2)
- ``stadia`` — hand-curated stadium coordinates + roof / cooled
  metadata (Phase 1 lookup; full enrichment Phase 4)

Per-source modules expose ``ingest(...)``-style entry points that
write into the canonical SQLite database (``thermofooty.config.SQLITE_PATH``).
"""
