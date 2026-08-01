from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


from .csv import render_csv
from .json import render_summary
from .markdown import render_markdown

__all__ = ["render_csv", "render_markdown", "render_summary", "write_json"]
