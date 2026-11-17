"""Experimental-design structures: cycles, dated assignments, resolution.

A design is a list of per-cycle revisions. Each cycle carries its own
assignments and anomaly rules, so revising one cycle never touches another.
Assignments may be dated (``effective_from``/``effective_to``), which lets a
plot change treatment group inside a cycle; the assignment in effect at the
measurement time wins.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Assignment:
    plot: str
    block: Optional[str]
    main_treatment: Optional[str]
    sub_treatment: Optional[str]
    effective_from: Optional[str]  # ISO date, inclusive; None = open
    effective_to: Optional[str]    # ISO date, exclusive; None = open


@dataclass(frozen=True)
class CycleDesign:
    cycle: str
    revision: int
    frozen: bool
    start_date: Optional[str]
    end_date: Optional[str]
    weather_start: Optional[str]
    weather_end: Optional[str]
    blocks: tuple
    assignments: tuple
    anomaly_rules: dict  # variable -> {"min": x|None, "max": y|None}


def _date(value):
    if value is None:
        return None
    return str(value)[:10]


def treatment_path(main, sub):
    head = main if main is not None else "unassigned"
    return "%s/%s" % (head, sub) if sub is not None else head


def parse_cycles(raw_cycles):
    cycles = []
    for rc in raw_cycles:
        name = rc.get("cycle") or rc.get("name")
        if not name:
            raise ValueError("design cycle requires a 'cycle' name")
        assignments = []
        for a in rc.get("assignments", []):
            if not isinstance(a, dict) or a.get("plot") is None:
                continue
            assignments.append(Assignment(
                plot=str(a["plot"]),
                block=a.get("block"),
                main_treatment=a.get("main_treatment") or a.get("treatment"),
                sub_treatment=a.get("sub_treatment"),
                effective_from=_date(a.get("effective_from")),
                effective_to=_date(a.get("effective_to")),
            ))
        rules = {}
        for var, rule in (rc.get("anomaly_rules") or {}).items():
            if isinstance(rule, dict):
                rules[var] = {"min": rule.get("min"), "max": rule.get("max")}
        window = rc.get("weather_window") or {}
        cycles.append(CycleDesign(
            cycle=str(name),
            revision=int(rc.get("revision", 1)),
            frozen=bool(rc.get("frozen", False)),
            start_date=_date(rc.get("start_date")),
            end_date=_date(rc.get("end_date")),
            weather_start=_date(window.get("start")) or _date(rc.get("start_date")),
            weather_end=_date(window.get("end")) or _date(rc.get("end_date")),
            blocks=tuple(rc.get("blocks") or ()),
            assignments=tuple(assignments),
            anomaly_rules=rules,
        ))
    return cycles


def synthesize_legacy_cycle(records):
    """Degenerate single-cycle design for legacy payloads without a design."""
    seen = set()
    assignments = []
    blocks = set()
    for rec in records:
        if rec.get("block") is not None:
            blocks.add(rec["block"])
        plot = rec.get("plot")
        if plot is None or plot in seen:
            continue
        seen.add(plot)
        assignments.append(Assignment(
            plot=str(plot),
            block=rec.get("block"),
            main_treatment=rec.get("main_treatment") or rec.get("treatment"),
            sub_treatment=rec.get("sub_treatment"),
            effective_from=None,
            effective_to=None,
        ))
    return CycleDesign(
        cycle="legacy", revision=1, frozen=False,
        start_date=None, end_date=None, weather_start=None, weather_end=None,
        blocks=tuple(sorted(blocks)), assignments=tuple(assignments),
        anomaly_rules={},
    )


def resolve_assignment(design, plot, measured_at):
    """Assignment in effect for ``plot`` at ``measured_at`` (ISO date/time).

    Dated assignments let a plot change group mid-cycle. Without a
    measurement time the latest-assigned group is used (deterministic).
    Returns None when no assignment covers the measurement time.
    """
    day = _date(measured_at)
    cands = [a for a in design.assignments if a.plot == plot]
    if not cands:
        return None
    if day is not None:
        bounded = [
            a for a in cands
            if (a.effective_from is None or a.effective_from <= day)
            and (a.effective_to is None or day < a.effective_to)
        ]
        if not bounded:
            return None
        cands = bounded
    return max(cands, key=lambda a: (a.effective_from or "", a.effective_to or "9999-12-31"))


def config_dict(design):
    return {
        "cycle": design.cycle,
        "revision": design.revision,
        "blocks": list(design.blocks),
        "assignments": [dict(a.__dict__) for a in design.assignments],
        "anomaly_rules": design.anomaly_rules,
        "start_date": design.start_date,
        "end_date": design.end_date,
    }


def config_hash(design):
    return digest(canonical_json(config_dict(design)))
