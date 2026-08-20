from __future__ import annotations

from . import write_json


def render_summary(*, run_id: str, period: dict, observed_at: str, publishable: bool, aggregate: dict) -> dict:
    return {"schema_version": "1.0", "run_id": run_id, "period": period, "observed_at": observed_at, "publishable": publishable, **aggregate}
