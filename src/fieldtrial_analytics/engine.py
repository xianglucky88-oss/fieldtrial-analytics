"""Domain calculations.

``calculate`` is the original naive aggregator, kept only for backward
compatibility. Everything below it is the design-driven builder: it turns
raw observations into model-input rows that preserve cycle, block/plot,
treatment hierarchy, unit, measurement time and missing reason, then folds
those rows into stratified summaries. Missing (``value: null`` with a
``missing_reason``) is never folded into numeric zero; real zeros stay in
the analysis.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .design import canonical_json, config_hash, digest, resolve_assignment, treatment_path
from .units import UnitConversionError, canonical_unit, convert_value

DEFAULT_VARIABLE = "value"
UNASSIGNED = "unassigned"


# ---------------------------------------------------------------------------
# legacy aggregator (kept for backward compatibility)
# ---------------------------------------------------------------------------
def calculate(rows, payload):
    groups = {}; missing=[]
    for row in rows:
        if row.get("value") is None: missing.append(row["plot"]); continue
        groups.setdefault(row["treatment"], []).append(float(row["value"]))
    means = {k: round(sum(v)/len(v), 3) for k,v in groups.items()}
    return {"treatment_means": means, "observed_count": sum(map(len, groups.values())), "missing_plots": missing, "blocks": sorted({r["block"] for r in rows})}


# ---------------------------------------------------------------------------
# normalization, observation identity, idempotent deduplication
# ---------------------------------------------------------------------------
def normalize_record(rec, default_cycle):
    """Normalize a raw record. Returns ``(norm, None)`` or ``(None, problem)``."""
    plot = rec.get("plot")
    cycle = rec.get("cycle") or default_cycle
    norm = {
        "obs_id": rec.get("obs_id"),
        "cycle": str(cycle) if cycle is not None else None,
        "plot": str(plot) if plot is not None else None,
        "block": rec.get("block"),
        "main_treatment": rec.get("main_treatment") or rec.get("treatment"),
        "sub_treatment": rec.get("sub_treatment"),
        "variable": str(rec.get("variable") or DEFAULT_VARIABLE),
        "measured_at": rec.get("measured_at"),
        "value": rec.get("value"),
        "unit": rec.get("unit"),
        "area_value": rec.get("area_value"),
        "area_unit": rec.get("area_unit"),
        "missing_reason": rec.get("missing_reason"),
        "exclude": bool(rec.get("exclude", False)),
        "exclusion_reason": rec.get("exclusion_reason"),
    }
    if not norm["obs_id"]:
        # Identity from content: replaying the same observation yields the
        # same id, so duplicate submissions dedupe idempotently.
        fingerprint = {k: norm[k] for k in ("cycle", "plot", "variable", "measured_at", "value", "unit")}
        norm["obs_id"] = "auto-" + digest(canonical_json(fingerprint))
    if norm["plot"] is None:
        return None, {"obs_id": norm["obs_id"], "cycle": norm["cycle"], "plot": None,
                      "variable": norm["variable"], "reason": "missing_plot", "detail": {}}
    if norm["cycle"] is None:
        return None, {"obs_id": norm["obs_id"], "cycle": None, "plot": norm["plot"],
                      "variable": norm["variable"], "reason": "missing_cycle", "detail": {}}
    return norm, None


def dedupe_records(norm_records):
    """Deduplicate by observation identity.

    The kept record is the canonically smallest one, so the result does not
    depend on input order. Conflicting duplicates (same id, different
    content) are flagged, never silently merged.
    """
    groups = defaultdict(list)
    for rec in norm_records:
        groups[rec["obs_id"]].append(rec)
    unique, duplicates = [], []
    for obs_id in sorted(groups):
        recs = sorted(groups[obs_id], key=canonical_json)
        unique.append(recs[0])
        if len(recs) > 1:
            distinct = {canonical_json(r) for r in recs}
            duplicates.append({
                "obs_id": obs_id,
                "dropped_count": len(recs) - 1,
                "conflict": len(distinct) > 1,
                "kept_value": recs[0]["value"],
                "dropped_values": (
                    sorted({canonical_json(r["value"]) for r in recs[1:]})
                    if len(distinct) > 1 else []
                ),
            })
    return unique, duplicates


# ---------------------------------------------------------------------------
# model-input rows
# ---------------------------------------------------------------------------
def build_row(rec, design):
    """Build one model-input row; may also yield an exclusion entry."""
    assignment = resolve_assignment(design, rec["plot"], rec["measured_at"])
    block, main, sub = rec["block"], rec["main_treatment"], rec["sub_treatment"]
    if assignment is not None:
        # The design is authoritative for the stratum at measurement time.
        if assignment.block is not None:
            block = assignment.block
        if assignment.main_treatment is not None:
            main = assignment.main_treatment
        if assignment.sub_treatment is not None:
            sub = assignment.sub_treatment
    row = {
        "obs_id": rec["obs_id"],
        "cycle": rec["cycle"],
        "plot": rec["plot"],
        "block": block,
        "main_treatment": main,
        "sub_treatment": sub,
        "treatment_path": treatment_path(main, sub),
        "variable": rec["variable"],
        "measured_at": rec["measured_at"],
        "raw_value": rec["value"],
        "raw_unit": rec["unit"],
        "area_value": rec["area_value"],
        "area_unit": rec["area_unit"],
        "value": None,
        "unit": canonical_unit(rec["unit"]),
        "conversion": None,
        "missing_reason": None,
        "exclusion_reason": None,
        "included": False,
        "measure_index": None,
    }
    raw_value = rec["value"]
    if raw_value is None:
        # Structural missingness: kept as a row with a reason, never as zero.
        row["missing_reason"] = rec["missing_reason"] or "unspecified"
        return row, None
    try:
        number = float(raw_value)
    except (TypeError, ValueError):
        row["exclusion_reason"] = "non_numeric_value"
        return row, _exclusion(row, "non_numeric_value", {"raw_value": raw_value})
    try:
        value, unit, note = convert_value(number, rec["unit"], rec["area_value"], rec["area_unit"])
    except (UnitConversionError, TypeError, ValueError) as exc:
        row["exclusion_reason"] = "unit_not_convertible"
        return row, _exclusion(row, "unit_not_convertible", {"message": str(exc)})
    row["value"] = round(value, 6)
    row["unit"] = unit
    row["conversion"] = note
    if rec["exclude"]:
        row["exclusion_reason"] = rec["exclusion_reason"] or "manual"
        return row, _exclusion(row, row["exclusion_reason"], {})
    rule = design.anomaly_rules.get(rec["variable"])
    if rule:
        lo, hi = rule.get("min"), rule.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            row["exclusion_reason"] = "out_of_range"
            return row, _exclusion(row, "out_of_range", {
                "rule": {"variable": rec["variable"], "min": lo, "max": hi},
                "value": row["value"], "unit": unit,
            })
    row["included"] = True
    return row, None


def _exclusion(row, reason, detail):
    return {"obs_id": row["obs_id"], "cycle": row["cycle"], "plot": row["plot"],
            "variable": row["variable"], "reason": reason, "detail": detail}


def measure_indices(rows):
    """Sequence repeated measures per (cycle, plot, variable) by time."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["cycle"], row["plot"], row["variable"])].append(row)
    for key in groups:
        ordered = sorted(groups[key], key=lambda r: (r["measured_at"] or "", r["obs_id"]))
        for index, row in enumerate(ordered, 1):
            row["measure_index"] = index


def sort_rows(rows):
    return sorted(rows, key=lambda r: (
        r["cycle"], r["plot"], r["variable"], r["measured_at"] or "", r["obs_id"],
    ))


# ---------------------------------------------------------------------------
# stratified summaries (computed from exactly the included rows)
# ---------------------------------------------------------------------------
def _stats(values):
    return {"n": len(values), "mean": round(sum(values) / len(values), 3),
            "total": round(sum(values), 3)}


def _strata(rows, keyfn):
    out = defaultdict(list)
    for row in rows:
        out[keyfn(row)].append(row["value"])
    return {k: _stats(v) for k, v in sorted(out.items())}


def stratified_summary(rows):
    """Per (variable, canonical unit) folds over included rows only."""
    groups = defaultdict(list)
    for row in rows:
        if row["included"]:
            groups[(row["variable"], row["unit"])].append(row)
    summary = {}
    for (variable, unit), rs in sorted(groups.items()):
        summary.setdefault(variable, {})[unit] = {
            "overall": _stats([r["value"] for r in rs]),
            "by_cycle": _strata(rs, lambda r: r["cycle"]),
            "by_treatment": _strata(rs, lambda r: r["treatment_path"]),
            "by_main_treatment": _strata(rs, lambda r: r["main_treatment"] or UNASSIGNED),
            "by_block": _strata(rs, lambda r: r["block"] or UNASSIGNED),
            "by_block_treatment": _strata(
                rs, lambda r: "%s|%s" % (r["block"] or UNASSIGNED, r["treatment_path"])),
            "by_cycle_treatment": _strata(
                rs, lambda r: "%s|%s" % (r["cycle"], r["treatment_path"])),
        }
    return summary


def missing_summary(rows):
    """Missing rows counted by reason — kept distinct from numeric zero."""
    counts = defaultdict(int)
    for row in rows:
        if row["missing_reason"]:
            counts[row["missing_reason"]] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# design cells (structural missingness / partial cycles)
# ---------------------------------------------------------------------------
def compute_cells(cycles, rows):
    cells, status = [], {}
    for cd in cycles:
        expected = {
            (a.block or UNASSIGNED, treatment_path(a.main_treatment, a.sub_treatment))
            for a in cd.assignments
        }
        observed = {
            (r["block"] or UNASSIGNED, r["treatment_path"])
            for r in rows if r["cycle"] == cd.cycle
        }
        gap_cells = 0
        for block, path in sorted(expected | observed):
            cell_rows = [
                r for r in rows
                if r["cycle"] == cd.cycle
                and (r["block"] or UNASSIGNED) == block
                and r["treatment_path"] == path
            ]
            included = sum(1 for r in cell_rows if r["included"])
            is_expected = (block, path) in expected
            state = "ok" if included > 0 else ("gap" if is_expected else "undesigned")
            if not is_expected:
                state = "undesigned"
            if state == "gap":
                gap_cells += 1
            cells.append({
                "cycle": cd.cycle, "block": block, "treatment_path": path,
                "expected": is_expected, "rows": len(cell_rows),
                "included": included, "status": state,
            })
        cycle_rows = [r for r in rows if r["cycle"] == cd.cycle]
        status[cd.cycle] = {
            "revision": cd.revision,
            "frozen": cd.frozen,
            "expected_cells": len(expected),
            "gap_cells": gap_cells,
            "complete": gap_cells == 0,
            "observations": len(cycle_rows),
            "included": sum(1 for r in cycle_rows if r["included"]),
        }
    return cells, status


# ---------------------------------------------------------------------------
# weather gaps (a gap is flagged, never zero-filled)
# ---------------------------------------------------------------------------
def find_weather_gaps(weather_records, cycles):
    invalid = []
    by_cycle_date = {}
    for rec in weather_records:
        day = str(rec.get("date") or "")[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            invalid.append({"record": rec, "reason": "invalid_date"})
            continue
        cycle = rec.get("cycle")
        if cycle is None:
            for cd in cycles:
                if (cd.weather_start and cd.weather_end
                        and cd.weather_start <= day <= cd.weather_end):
                    cycle = cd.cycle
                    break
        if cycle is None and len(cycles) == 1:
            cycle = cycles[0].cycle
        if cycle is None:
            invalid.append({"record": rec, "reason": "unassigned_cycle"})
            continue
        by_cycle_date[(cycle, day)] = rec
    gaps = []
    for cd in cycles:
        if not (cd.weather_start and cd.weather_end):
            continue
        try:
            start = date.fromisoformat(cd.weather_start)
            end = date.fromisoformat(cd.weather_end)
        except ValueError:
            continue
        day = start
        while day <= end:
            key = day.isoformat()
            rec = by_cycle_date.get((cd.cycle, key))
            if rec is None:
                gaps.append({"cycle": cd.cycle, "date": key, "type": "missing_record"})
            else:
                missing_fields = sorted(
                    k for k, v in rec.items() if k not in ("date", "cycle") and v is None
                )
                if missing_fields:
                    gaps.append({"cycle": cd.cycle, "date": key,
                                 "type": "partial_record", "missing_fields": missing_fields})
            day += timedelta(days=1)
    gaps.sort(key=lambda g: (g["cycle"], g["date"]))
    return gaps, invalid


# ---------------------------------------------------------------------------
# per-cycle snapshots (traceable, idempotent, versioned)
# ---------------------------------------------------------------------------
def snapshot_metas(cycles, rows, priors, reused_cycles):
    metas = []
    for cd in cycles:
        prior = priors.get(cd.cycle, {})
        cycle_rows = [r for r in rows if r["cycle"] == cd.cycle]
        obs_ids = sorted(r["obs_id"] for r in cycle_rows)
        if cd.cycle in reused_cycles:
            metas.append({
                "cycle": cd.cycle,
                "snapshot_id": prior.get("snapshot_id"),
                "version": prior.get("version"),
                "frozen": True,
                "reused": True,
                "row_count": len(cycle_rows),
                "obs_ids": obs_ids,
                "config_hash": prior.get("config_hash"),
            })
            continue
        cfg_hash = config_hash(cd)
        fingerprint = digest(canonical_json(cycle_rows))
        snapshot_id = "snap-" + digest("%s|%s|%s" % (cd.cycle, cfg_hash, fingerprint))
        if prior.get("snapshot_id") == snapshot_id:
            version = prior.get("version", cd.revision)  # idempotent replay
        elif prior:
            version = int(prior.get("version", 0)) + 1    # design/data revision
        else:
            version = cd.revision
        metas.append({
            "cycle": cd.cycle,
            "snapshot_id": snapshot_id,
            "version": version,
            "frozen": cd.frozen,
            "reused": False,
            "row_count": len(cycle_rows),
            "obs_ids": obs_ids,
            "config_hash": cfg_hash,
        })
    return metas


# ---------------------------------------------------------------------------
# consistency: prove rows, strata and summaries agree
# ---------------------------------------------------------------------------
def consistency_report(rows, exclusions, invalid_records, summary, snapshots):
    included = sum(1 for r in rows if r["included"])
    excluded = sum(1 for r in rows if r["exclusion_reason"])
    missing = sum(1 for r in rows if r["missing_reason"])
    stratified = {}
    stratified_ok = True
    for variable, units in summary.items():
        for unit, group in units.items():
            rows_n = sum(
                1 for r in rows
                if r["included"] and r["variable"] == variable and r["unit"] == unit
            )
            match = group["overall"]["n"] == rows_n
            stratified_ok = stratified_ok and match
            stratified["%s|%s" % (variable, unit)] = {
                "summary_n": group["overall"]["n"], "rows_n": rows_n, "match": match,
            }
    checks = {
        "row_count": len(rows),
        "included_count": included,
        "excluded_count": excluded,
        "missing_count": missing,
        "partition_complete": included + excluded + missing == len(rows),
        "exclusions_listed": len(exclusions) == excluded + len(invalid_records),
        "snapshot_rows_union": sum(m["row_count"] for m in snapshots) == len(rows),
        "missing_rows_have_null_value": all(
            r["value"] is None for r in rows if r["missing_reason"]
        ),
        "stratified_counts": stratified,
    }
    checks["ok"] = all([
        checks["partition_complete"],
        checks["exclusions_listed"],
        checks["snapshot_rows_union"],
        checks["missing_rows_have_null_value"],
        stratified_ok,
    ])
    return checks
