from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..canonical import canonical_json


def write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


from .csv import render_csv
from .json import render_summary
from .markdown import render_markdown

__all__ = ["render_csv", "render_markdown", "render_summary", "write_json"]
