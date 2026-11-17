"""Unit registry and conversions.

Conversions never mutate the original evidence: the raw value/unit/area stay
on the record and only a derived canonical value is produced alongside them.
Unknown units pass through unchanged (canonical unit == the unit itself), so
nothing is ever silently rescaled.
"""
from __future__ import annotations

# Area units -> square metres. 1 mu = 2000/3 m2 (666.666...), 1 ha = 10000 m2.
AREA_TO_M2 = {"m2": 1.0, "mu": 2000.0 / 3.0, "ha": 10000.0}

# Yield-rate units -> kg/ha. 1 ha == 15 mu, so kg/mu x 15 == kg/ha.
RATE_TO_KG_HA = {"kg/ha": 1.0, "t/ha": 1000.0, "kg/mu": 15.0, "g/m2": 10.0}

# Plot-level harvest totals that need the harvested area to become a rate.
PLOT_MASS_TO_KG = {"kg/plot": 1.0, "t/plot": 1000.0}


class UnitConversionError(ValueError):
    """Raised when a value cannot be converted to its canonical unit."""


def _norm(unit):
    if unit is None:
        return None
    return str(unit).strip().lower()


def canonical_unit(unit):
    """Canonical unit for ``unit`` without converting a value."""
    u = _norm(unit)
    if u is None:
        return "unitless"
    if u in RATE_TO_KG_HA or u in PLOT_MASS_TO_KG:
        return "kg/ha"
    if u in AREA_TO_M2:
        return "m2"
    return u


def convert_value(value, unit, area_value=None, area_unit=None):
    """Convert ``value`` to its canonical unit.

    Returns ``(canonical_value, canonical_unit, conversion_note_or_None)``.
    Raises :class:`UnitConversionError` when a plot-level total lacks a
    usable area. Unknown units pass through unchanged.
    """
    u = _norm(unit)
    if u is None:
        return float(value), "unitless", None
    if u in RATE_TO_KG_HA:
        factor = RATE_TO_KG_HA[u]
        note = None if factor == 1.0 else {"from": u, "to": "kg/ha", "factor": factor}
        return float(value) * factor, "kg/ha", note
    if u in PLOT_MASS_TO_KG:
        au = _norm(area_unit)
        if area_value is None or au not in AREA_TO_M2:
            raise UnitConversionError(
                "unit %r requires harvested area (area_value/area_unit)" % u
            )
        area_m2 = float(area_value) * AREA_TO_M2[au]
        if area_m2 <= 0:
            raise UnitConversionError("harvested area must be positive")
        kg = float(value) * PLOT_MASS_TO_KG[u]
        note = {"from": u, "to": "kg/ha", "area_m2": round(area_m2, 6)}
        return kg / area_m2 * 10000.0, "kg/ha", note
    if u in AREA_TO_M2:
        factor = AREA_TO_M2[u]
        note = None if u == "m2" else {"from": u, "to": "m2", "factor": factor}
        return float(value) * factor, "m2", note
    return float(value), u, None
