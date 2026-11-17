def load_records(payload: dict) -> list[dict]:
    rows = payload.get("observations", [])
    if not isinstance(rows, list):
        raise ValueError("observations must be a list")
    return [dict(row) for row in rows if isinstance(row, dict)]


def load_cycles(payload: dict) -> list[dict]:
    """Raw design-cycle dicts from ``design.cycles`` or top-level ``cycles``."""
    design = payload.get("design")
    raw = design.get("cycles") if isinstance(design, dict) else None
    if raw is None:
        raw = payload.get("cycles")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("cycles must be a list")
    return [dict(c) for c in raw if isinstance(c, dict)]


def load_weather(payload: dict) -> list[dict]:
    rows = payload.get("weather", [])
    if not isinstance(rows, list):
        raise ValueError("weather must be a list")
    return [dict(row) for row in rows if isinstance(row, dict)]


def load_prior_snapshots(payload: dict) -> dict:
    """Previous run's per-cycle snapshots, keyed by cycle name.

    Used to keep frozen cycles byte-identical and to version revisions of
    unfrozen cycles. Each entry may carry ``snapshot_id``, ``version``,
    ``config_hash``, ``rows`` and ``exclusions``.
    """
    raw = payload.get("prior_snapshots", {})
    if not isinstance(raw, dict):
        raise ValueError("prior_snapshots must be an object")
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
