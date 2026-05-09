from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from midas_cn.models import DailyReport
from midas_cn.reports.markdown import MarkdownReportRenderer


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    return str(value)


class DailyReportArchive:
    def __init__(self, archive_dir: Path, renderer: MarkdownReportRenderer | None = None):
        self.archive_dir = archive_dir
        self.renderer = renderer or MarkdownReportRenderer()

    def save(self, report: DailyReport) -> tuple[Path, Path]:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        filename = f"chinese_report_{report.run_id}"
        json_path = self.archive_dir / f"{filename}.json"
        markdown_path = self.archive_dir / f"{filename}.md"
        json_path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        markdown_path.write_text(self.renderer.render(report), encoding="utf-8")
        return json_path, markdown_path
