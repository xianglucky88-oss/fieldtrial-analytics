# Field Trial Analytics

Aggregate blocked field-trial observations while preserving design and missingness.

## Capability

The design-driven pipeline turns randomized-block / split-plot trial records
(treatment plans, weather, fertilization, disease, harvest) into
analysis-ready model inputs plus stratified summaries:

- Every observation keeps its experiment cycle, plot/block, treatment
  hierarchy (`main_treatment`/`sub_treatment` for split-plot), unit,
  measurement time and missing reason in a long-format model-input row.
- **Missing is not zero.** `value: null` keeps its `missing_reason` and is
  excluded from means but counted in `missing_by_reason`; a real `0` stays
  in the analysis.
- **Plots may change group mid-cycle.** Dated assignments
  (`effective_from`/`effective_to`) resolve the treatment in effect at each
  observation's measurement time.
- **Repeated measures** per plot are all retained and sequenced
  (`measure_index`) by measurement time, independent of input order.
- **Units convert, evidence stays.** `kg/ha`, `t/ha`, `kg/mu`, `g/m2`,
  `kg/plot` + harvested area (`m2`/`mu`/`ha`) convert to canonical `kg/ha`;
  the raw value/unit/area are preserved unchanged next to the converted
  value. Unknown units pass through untouched.
- **Anomalies are excluded, listed, and reversible.** Per-cycle
  `anomaly_rules` (min/max per variable) and manual `exclude` flags set
  `exclusion_reason`; every excluded observation appears in `exclusions`.
- **Duplicates are idempotent** by observation identity (`obs_id`, or a
  deterministic content hash). Conflicting duplicates are flagged, never
  silently merged.
- **Design revisions are cycle-scoped.** Each cycle is versioned
  independently; `frozen` cycles are adopted verbatim from
  `prior_snapshots` and any new data or config change targeting them lands
  in `rejected_revisions`.

## Strategy: per-cycle snapshots

Chosen over one-shot batch and per-plot streaming aggregation. The cycle is
the freeze/revision boundary, so each cycle is canonicalized (sorted by
observation identity), fingerprinted and versioned as a snapshot
(`snapshots[]` with `snapshot_id`, `version`, `config_hash`, `obs_ids`).
Replaying or reshuffling the input converges to identical snapshot ids;
per-plot streaming is a special case of the same canonical fold; a one-shot
batch cannot express revisions or freezing. Summaries are folded from
exactly the included model-input rows, and the `consistency` block proves
row counts, stratification and summary values agree.

## Input schema (v2)

```json
{
  "cycles": [{"cycle": "2025-S1", "revision": 2, "frozen": false,
              "blocks": ["B1"], "assignments": [{"plot": "P1", "block": "B1",
              "main_treatment": "N0", "sub_treatment": "V1",
              "effective_from": "2025-03-01"}],
              "anomaly_rules": {"yield": {"min": 0, "max": 9000}},
              "weather_window": {"start": "2025-06-01", "end": "2025-06-07"}}],
  "observations": [{"obs_id": "o1", "cycle": "2025-S1", "plot": "P1",
                    "variable": "yield", "value": 320, "unit": "kg/mu",
                    "measured_at": "2025-06-21", "missing_reason": null}],
  "weather": [{"cycle": "2025-S1", "date": "2025-06-01", "rain_mm": 4}],
  "prior_snapshots": {"2024-S1": {"snapshot_id": "...", "version": 3,
                                  "config_hash": "...", "rows": [],
                                  "exclusions": []}}
}
```

Output: `model_input` (rows + counts), `stratified_summary` (per variable
and canonical unit: overall / by cycle / treatment / block / block×treatment
/ cycle×treatment), `missing_by_reason`, `cells` + `cycles` (structural
gaps, partial cycles), `exclusions`, `duplicates`, `weather_gaps`,
`snapshots`, `rejected_revisions`, `consistency`.

Payloads without `cycles` still run: a degenerate single-cycle design is
synthesized and the legacy keys (`treatment_means`, `observed_count`,
`missing_plots`, `blocks`) are emitted alongside the v2 output.

## Modules

- `src/fieldtrial_analytics/models.py`: immutable result contract.
- `src/fieldtrial_analytics/parser.py`: input validation and record extraction.
- `src/fieldtrial_analytics/design.py`: cycles, dated assignments, resolution.
- `src/fieldtrial_analytics/units.py`: unit registry and conversions.
- `src/fieldtrial_analytics/engine.py`: domain calculation only.
- `src/fieldtrial_analytics/report.py`: stable output envelope.
- `src/fieldtrial_analytics/pipeline.py`: orchestration boundary.
- `src/fieldtrial_analytics/cli.py`: deterministic JSON CLI.

## Run

```bash
PYTHONPATH=src python3 -m fieldtrial_analytics.cli                              # legacy fixture
PYTHONPATH=src python3 -m fieldtrial_analytics.cli fixtures/design_input.json   # design fixture
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The fixtures are local and deterministic; no network or credentials are required.
