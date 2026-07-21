# Phase-0 provenance (read-only audit tooling)

One-off scripts used to BUILD the Phase-0 baseline. Kept for provenance only —
they are **not part of the test suite** and are not re-run by CI. All are
read-only (a prod `SELECT`, or local CSV classification). Paths inside are
absolute to this checkout at authoring time.

- `pmcc_pull3.py` — read-only prod `SELECT` dumping every `robinhood_pmcc`
  `proposed_order` leg to `/tmp/pmcc_legs.csv` (single SELECT; no prod writes).
- `pmcc_classify_leap.py` — groups legs by pair; computes the B4 subtype split
  and the data-backed `cost_ignorant_leap_roll` count.
- `pmcc_naked_drill.py` — drills the fully_naked events into flat / naked_short
  (result: 20 flat, 0 naked_short, 4 reclassified to uncovered).
- `pmcc_enrich_csv.py` — folds the LEAP facts (`old_leap_px`, `has_new_leap`,
  `sold_leap`, `closed_short`, `b4_subtype`) into `planning/pmcc_rec_history.csv`.

Data outputs live under `planning/`:
- `pmcc_legs.csv` — the raw leg dump (279 legs).
- `pmcc_rec_history_leap.csv` — rec-level LEAP classification.

The authoritative baseline is `planning/pmcc_rec_history.csv` + the detectors in
`../detectors.py`; these scripts document how it was derived.
