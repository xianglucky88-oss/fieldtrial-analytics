"""Orchestration boundary.

Strategy: per-cycle snapshots. The cycle is the freeze/revision boundary, so
each cycle is canonicalized (deterministically sorted by observation
identity), fingerprinted and versioned as a snapshot. Replaying or
reshuffling the input converges to the same snapshot ids; frozen cycles are
adopted verbatim from ``prior_snapshots`` and never rewritten; revisions
only touch their own cycle. Per-plot streaming aggregation is a special
case of the same canonical fold, and a one-shot batch cannot express
revisions or freezing.
"""
from __future__ import annotations

from .design import config_hash, parse_cycles, synthesize_legacy_cycle
from .engine import (
    build_row,
    compute_cells,
    consistency_report,
    dedupe_records,
    find_weather_gaps,
    measure_indices,
    missing_summary,
    normalize_record,
    snapshot_metas,
    sort_rows,
    stratified_summary,
)
from .parser import load_cycles, load_prior_snapshots, load_records, load_weather
from .report import build_result

PROJECT = "fieldtrial-analytics"
STRATEGY = "cycle_snapshot"
SCHEMA = "fieldtrial/v2"


def run(payload: dict) -> dict:
    records = load_records(payload)
    raw_cycles = load_cycles(payload)
    weather = load_weather(payload)
    priors = load_prior_snapshots(payload)

    legacy = not raw_cycles
    if legacy:
        cycles = [synthesize_legacy_cycle(records)]
        for rec in records:
            rec.setdefault("cycle", cycles[0].cycle)
    else:
        cycles = parse_cycles(raw_cycles)
    cycle_by_name = {cd.cycle: cd for cd in cycles}
    default_cycle = cycles[0].cycle if len(cycles) == 1 else None

    normalized, invalid = [], []
    for rec in records:
        norm, problem = normalize_record(rec, default_cycle)
        if problem is not None:
            invalid.append(problem)
        else:
            normalized.append(norm)
    unique, duplicates = dedupe_records(normalized)

    # Frozen cycles with a prior snapshot: adopt rows verbatim, reject any
    # new submissions or config revisions targeting them.
    reused_cycles, adopted_rows, adopted_exclusions, rejected = set(), [], [], []
    for cd in cycles:
        prior = priors.get(cd.cycle)
        if not (cd.frozen and prior is not None):
            continue
        reused_cycles.add(cd.cycle)
        adopted_rows.extend(dict(r) for r in prior.get("rows", []))
        adopted_exclusions.extend(dict(e) for e in prior.get("exclusions", []))
        current_cfg = config_hash(cd)
        if prior.get("config_hash") not in (None, current_cfg):
            rejected.append({
                "type": "config_revision_rejected",
                "cycle": cd.cycle,
                "reason": "frozen_config_revision_rejected",
                "prior_config_hash": prior.get("config_hash"),
                "current_config_hash": current_cfg,
            })

    to_build = []
    for norm in unique:
        cd = cycle_by_name.get(norm["cycle"])
        if cd is None:
            invalid.append({
                "obs_id": norm["obs_id"], "cycle": norm["cycle"], "plot": norm["plot"],
                "variable": norm["variable"], "reason": "unknown_cycle",
                "detail": {"cycle": norm["cycle"]},
            })
            continue
        if cd.cycle in reused_cycles:
            rejected.append({
                "type": "observation_rejected",
                "obs_id": norm["obs_id"], "cycle": cd.cycle,
                "reason": "cycle_frozen",
            })
            continue
        to_build.append((norm, cd))

    built_rows, exclusions = [], []
    for norm, cd in to_build:
        row, exclusion = build_row(norm, cd)
        built_rows.append(row)
        if exclusion is not None:
            exclusions.append(exclusion)
    measure_indices(built_rows)

    rows = sort_rows(built_rows + adopted_rows)
    exclusions.extend(adopted_exclusions)
    exclusions.extend(invalid)
    exclusions.sort(key=lambda e: (
        str(e.get("cycle") or ""), str(e.get("plot") or ""), str(e.get("obs_id") or "")))
    rejected.sort(key=lambda e: (str(e.get("cycle") or ""), str(e.get("type") or "")))

    summary = stratified_summary(rows)
    cells, cycles_status = compute_cells(cycles, rows)
    weather_gaps, weather_invalid = find_weather_gaps(weather, cycles)
    snapshots = snapshot_metas(cycles, rows, priors, reused_cycles)

    data = {
        "schema": SCHEMA,
        "strategy": STRATEGY,
        "model_input": {
            "row_count": len(rows),
            "included_count": sum(1 for r in rows if r["included"]),
            "excluded_count": sum(1 for r in rows if r["exclusion_reason"]),
            "missing_count": sum(1 for r in rows if r["missing_reason"]),
            "rows": rows,
        },
        "stratified_summary": summary,
        "missing_by_reason": missing_summary(rows),
        "cells": cells,
        "cycles": cycles_status,
        "exclusions": exclusions,
        "duplicates": duplicates,
        "weather_gaps": weather_gaps,
        "weather_records_invalid": weather_invalid,
        "snapshots": snapshots,
        "rejected_revisions": rejected,
    }
    data["consistency"] = consistency_report(rows, exclusions, invalid, summary, snapshots)
    if legacy:
        data.update(_legacy_view(rows, summary))
    return build_result(PROJECT, data)


def _legacy_view(rows, summary):
    means = {}
    for unit_group in summary.get("value", {}).values():
        for path, stats in unit_group["by_treatment"].items():
            means[path] = stats["mean"]
    return {
        "treatment_means": means,
        "observed_count": sum(1 for r in rows if r["included"]),
        "missing_plots": sorted({r["plot"] for r in rows if r["missing_reason"]}),
        "blocks": sorted({r["block"] for r in rows if r["block"]}),
    }
