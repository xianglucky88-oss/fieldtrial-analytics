"""Validation for the design-driven pipeline.

One fixture batch deliberately mixes a complete split-plot design,
structural missingness, mixed units, a mid-cycle plot group change,
duplicates, anomalies, weather gaps, a partial cycle and a frozen cycle,
so the tests below prove the output is not a naive average stacking.
"""
import copy
import json
import random
import unittest
from pathlib import Path

from fieldtrial_analytics.pipeline import run

FIXTURE = Path(__file__).parents[1] / "fixtures" / "design_input.json"


def load_payload():
    return json.loads(FIXTURE.read_text())


def rows_by_id(result):
    return {row["obs_id"]: row for row in result["model_input"]["rows"]}


def snapshot_of(result, cycle):
    return next(s for s in result["snapshots"] if s["cycle"] == cycle)


class TestDesignPipeline(unittest.TestCase):
    def setUp(self):
        self.payload = load_payload()
        self.result = run(self.payload)

    def test_row_counts_and_consistency(self):
        mi = self.result["model_input"]
        self.assertEqual(mi["row_count"], 18)
        self.assertEqual(mi["included_count"], 15)
        self.assertEqual(mi["excluded_count"], 2)
        self.assertEqual(mi["missing_count"], 1)
        consistency = self.result["consistency"]
        self.assertTrue(consistency["ok"])
        self.assertTrue(consistency["partition_complete"])
        self.assertTrue(consistency["snapshot_rows_union"])
        self.assertTrue(consistency["exclusions_listed"])
        for check in consistency["stratified_counts"].values():
            self.assertTrue(check["match"])

    def test_missing_reason_distinct_from_zero(self):
        rows = rows_by_id(self.result)
        missing = rows["obs-p5-yield"]
        self.assertIsNone(missing["value"])
        self.assertEqual(missing["missing_reason"], "disease")
        self.assertFalse(missing["included"])
        zero = rows["obs-p7-yield"]
        self.assertEqual(zero["value"], 0.0)
        self.assertIsNone(zero["missing_reason"])
        self.assertTrue(zero["included"])
        self.assertEqual(self.result["missing_by_reason"], {"disease": 1})
        # N1/V1 = {P3: 5200, P7: 0, P9b: 5600}: the real zero is counted,
        # the missing P5 is not. Missing-as-zero would give 2700, dropping
        # the zero would give 5400.
        by_treatment = self.result["stratified_summary"]["yield"]["kg/ha"]["by_treatment"]
        self.assertEqual(by_treatment["N1/V1"], {"n": 3, "mean": 3600.0, "total": 10800.0})

    def test_unit_conversion_preserves_raw_evidence(self):
        rows = rows_by_id(self.result)
        p2 = rows["obs-p2-yield"]
        self.assertEqual((p2["value"], p2["unit"]), (4800.0, "kg/ha"))
        self.assertEqual((p2["raw_value"], p2["raw_unit"]), (320, "kg/mu"))
        p3 = rows["obs-p3-yield"]
        self.assertEqual(p3["value"], 5200.0)
        self.assertEqual((p3["raw_value"], p3["raw_unit"]), (5.2, "t/ha"))
        p4 = rows["obs-p4-yield"]
        self.assertEqual(p4["value"], 4800.0)
        self.assertEqual((p4["raw_value"], p4["raw_unit"]), (96, "kg/plot"))
        self.assertEqual((p4["area_value"], p4["area_unit"]), (0.3, "mu"))
        self.assertEqual(p4["conversion"]["area_m2"], 200.0)

    def test_stratified_summary_is_not_simple_average_stacking(self):
        summary = self.result["stratified_summary"]["yield"]["kg/ha"]
        s1 = summary["by_cycle"]["2025-S1"]
        self.assertEqual((s1["n"], s1["mean"]), (8, 4287.5))
        # Naive pooling of raw numbers (mixed units, anomaly included).
        raw_values = [4500, 320, 5.2, 96, 5100, 0, 12000, 4300, 5600]
        naive = round(sum(raw_values) / len(raw_values), 3)
        self.assertNotEqual(s1["mean"], naive)
        # Missing-as-zero pooling would give yet another number.
        self.assertNotEqual(s1["mean"], round(sum(raw_values) / (len(raw_values) + 1), 3))
        # Unweighted mean of stratum means != weighted stratified mean:
        # strata are not just stacked averages.
        strata = summary["by_treatment"]
        s1_strata = [strata[k]["mean"] for k in ("N0/V1", "N0/V2", "N1/V1", "N1/V2")]
        stacked = round(sum(s1_strata) / len(s1_strata), 3)
        self.assertEqual(stacked, 4437.5)
        self.assertNotEqual(s1["mean"], stacked)
        # Strata themselves differ, so stratification carries information.
        self.assertEqual(strata["N0/V1"]["mean"], 4400.0)
        self.assertEqual(strata["N0/V2"]["mean"], 4950.0)
        self.assertEqual(strata["N1/V2"]["mean"], 4800.0)

    def test_plot_group_change_within_cycle(self):
        rows = rows_by_id(self.result)
        early = rows["obs-p9a-yield"]   # measured 2025-05-20
        late = rows["obs-p9b-yield"]    # measured 2025-07-10
        self.assertEqual(early["treatment_path"], "N0/V1")
        self.assertEqual(late["treatment_path"], "N1/V1")
        self.assertEqual(early["plot"], "P9")
        self.assertEqual(late["plot"], "P9")
        # Both strata credit the same plot in the same cycle.
        by_treatment = self.result["stratified_summary"]["yield"]["kg/ha"]["by_treatment"]
        self.assertEqual(by_treatment["N0/V1"]["n"], 2)   # P1 + P9 early
        self.assertEqual(by_treatment["N1/V1"]["n"], 3)   # P3 + P7 + P9 late

    def test_repeated_measures_ordered_despite_scrambled_input(self):
        rows = rows_by_id(self.result)
        seq = sorted(
            (r["measure_index"], r["measured_at"], r["value"])
            for r in rows.values()
            if r["plot"] == "P1" and r["variable"] == "plant_height"
        )
        self.assertEqual(seq, [(1, "2025-04-10", 40.0), (2, "2025-04-25", 51.0),
                               (3, "2025-05-10", 62.0)])
        height = self.result["stratified_summary"]["plant_height"]["cm"]
        self.assertEqual(height["overall"], {"n": 3, "mean": 51.0, "total": 153.0})

    def test_duplicates_are_idempotent_by_identity(self):
        duplicates = {d["obs_id"]: d for d in self.result["duplicates"]}
        self.assertEqual(duplicates["obs-p2-yield"]["dropped_count"], 1)
        self.assertFalse(duplicates["obs-p2-yield"]["conflict"])
        conflict = duplicates["obs-p6-yield"]
        self.assertTrue(conflict["conflict"])
        self.assertEqual(conflict["kept_value"], 5100)  # deterministic keep
        rows = rows_by_id(self.result)
        self.assertEqual(rows["obs-p6-yield"]["value"], 5100.0)
        self.assertEqual(self.result["model_input"]["row_count"], 18)

    def test_exclusions_listed_with_reasons(self):
        exclusions = {e["obs_id"]: e for e in self.result["exclusions"]}
        self.assertEqual(set(exclusions), {"obs-p8-yield", "obs-p2-h1"})
        self.assertEqual(exclusions["obs-p8-yield"]["reason"], "out_of_range")
        self.assertEqual(exclusions["obs-p8-yield"]["detail"]["rule"]["max"], 9000)
        self.assertEqual(exclusions["obs-p2-h1"]["reason"], "lodging")
        rows = rows_by_id(self.result)
        self.assertFalse(rows["obs-p8-yield"]["included"])
        self.assertEqual(rows["obs-p8-yield"]["exclusion_reason"], "out_of_range")

    def test_weather_gaps_flagged_not_zero_filled(self):
        gaps = {(g["date"], g["type"]): g for g in self.result["weather_gaps"]}
        self.assertIn(("2025-06-03", "missing_record"), gaps)
        self.assertIn(("2025-06-06", "missing_record"), gaps)
        partial = gaps[("2025-06-02", "partial_record")]
        self.assertEqual(partial["missing_fields"], ["rain_mm"])
        # A recorded zero is data, not a gap.
        self.assertNotIn(("2025-06-04", "partial_record"), gaps)

    def test_partial_cycle_and_cells(self):
        cycles = self.result["cycles"]
        self.assertFalse(cycles["2025-S2"]["complete"])
        self.assertEqual(cycles["2025-S2"]["gap_cells"], 2)
        gap_cells = {(c["cycle"], c["block"], c["treatment_path"])
                     for c in self.result["cells"] if c["status"] == "gap"}
        self.assertIn(("2025-S2", "B2", "T1"), gap_cells)
        self.assertIn(("2025-S2", "B2", "T2"), gap_cells)
        # Structural missingness inside the otherwise complete 2025-S1:
        # P5 is missing (disease) and P8 was excluded as anomalous.
        self.assertIn(("2025-S1", "B2", "N0/V1"), gap_cells)
        self.assertIn(("2025-S1", "B2", "N1/V2"), gap_cells)
        self.assertFalse(cycles["2025-S1"]["complete"])
        self.assertTrue(cycles["2024-S1"]["complete"])

    def test_frozen_cycle_reused_and_revisions_rejected(self):
        snap = snapshot_of(self.result, "2024-S1")
        self.assertTrue(snap["reused"])
        self.assertEqual(snap["snapshot_id"], "snap-2024s1-v3")
        self.assertEqual(snap["version"], 3)
        rejected = self.result["rejected_revisions"]
        kinds = {(r["type"], r.get("obs_id")) for r in rejected}
        self.assertIn(("observation_rejected", "obs-2024-q3-yield"), kinds)
        self.assertIn(("config_revision_rejected", None), kinds)
        rows = rows_by_id(self.result)
        self.assertNotIn("obs-2024-q3-yield", rows)
        self.assertEqual(rows["obs-2024-q1-yield"]["value"], 4100.0)

    def test_replay_and_shuffle_are_deterministic(self):
        first = json.dumps(run(load_payload()), sort_keys=True)
        second = json.dumps(run(load_payload()), sort_keys=True)
        self.assertEqual(first, second)
        shuffled = load_payload()
        random.Random(20261117).shuffle(shuffled["observations"])
        third = json.dumps(run(shuffled), sort_keys=True)
        self.assertEqual(first, third)

    def test_threshold_revision_bumps_only_its_cycle(self):
        baseline = run(load_payload())
        s1_before = snapshot_of(baseline, "2025-S1")
        revised = load_payload()
        for cycle in revised["cycles"]:
            if cycle["cycle"] == "2025-S1":
                cycle["anomaly_rules"]["yield"]["max"] = 20000
        revised["prior_snapshots"]["2025-S1"] = {
            "snapshot_id": s1_before["snapshot_id"],
            "version": s1_before["version"],
            "config_hash": s1_before["config_hash"],
        }
        after = run(revised)
        rows = rows_by_id(after)
        self.assertTrue(rows["obs-p8-yield"]["included"])  # 12000 now in range
        by_treatment = after["stratified_summary"]["yield"]["kg/ha"]["by_treatment"]
        self.assertEqual(by_treatment["N1/V2"], {"n": 2, "mean": 8400.0, "total": 16800.0})
        s1_after = snapshot_of(after, "2025-S1")
        self.assertNotEqual(s1_after["snapshot_id"], s1_before["snapshot_id"])
        self.assertEqual(s1_after["version"], s1_before["version"] + 1)
        # Other cycles are untouched by the revision.
        self.assertEqual(snapshot_of(after, "2025-S2")["snapshot_id"],
                         snapshot_of(baseline, "2025-S2")["snapshot_id"])
        self.assertEqual(snapshot_of(after, "2024-S1")["snapshot_id"], "snap-2024s1-v3")
        self.assertTrue(after["consistency"]["ok"])

    def test_replay_with_prior_is_idempotent(self):
        baseline = run(load_payload())
        s1 = snapshot_of(baseline, "2025-S1")
        replayed = load_payload()
        replayed["prior_snapshots"]["2025-S1"] = {
            "snapshot_id": s1["snapshot_id"],
            "version": s1["version"],
            "config_hash": s1["config_hash"],
        }
        again = run(replayed)
        s1_again = snapshot_of(again, "2025-S1")
        self.assertEqual(s1_again["snapshot_id"], s1["snapshot_id"])
        self.assertEqual(s1_again["version"], s1["version"])  # no bump on replay

    def test_freezing_a_cycle_locks_rows_and_rejects_new_data(self):
        baseline = run(load_payload())
        s2 = snapshot_of(baseline, "2025-S2")
        s2_rows = [r for r in baseline["model_input"]["rows"] if r["cycle"] == "2025-S2"]
        frozen = load_payload()
        for cycle in frozen["cycles"]:
            if cycle["cycle"] == "2025-S2":
                cycle["frozen"] = True
                cycle["anomaly_rules"]["yield"]["max"] = 99999  # must be ignored
        frozen["observations"].append({
            "obs_id": "obs-p12-yield", "cycle": "2025-S2", "plot": "P12",
            "variable": "yield", "value": 4600, "unit": "kg/ha",
            "measured_at": "2025-09-16",
        })
        frozen["prior_snapshots"]["2025-S2"] = {
            "snapshot_id": s2["snapshot_id"], "version": s2["version"],
            "config_hash": s2["config_hash"], "rows": s2_rows, "exclusions": [],
        }
        after = run(frozen)
        rows = rows_by_id(after)
        self.assertNotIn("obs-p12-yield", rows)
        after_s2_rows = [r for r in after["model_input"]["rows"] if r["cycle"] == "2025-S2"]
        self.assertEqual(after_s2_rows, s2_rows)  # frozen rows byte-identical
        snap = snapshot_of(after, "2025-S2")
        self.assertTrue(snap["reused"])
        self.assertEqual(snap["snapshot_id"], s2["snapshot_id"])
        rejected = {(r["type"], r.get("obs_id")) for r in after["rejected_revisions"]}
        self.assertIn(("observation_rejected", "obs-p12-yield"), rejected)
        self.assertIn(("config_revision_rejected", None), rejected)
        self.assertTrue(after["consistency"]["ok"])


if __name__ == "__main__":
    unittest.main()
