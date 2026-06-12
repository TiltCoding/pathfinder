"""Reports domain service.

A small, dependency-free service used as an eval fixture. It aggregates a list of
record dicts (each with a numeric "value") into a report summary.
"""

from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class Report:
    id: str
    records: List[Dict] = field(default_factory=list)


class ReportService:
    """Builds report summaries from records."""

    def __init__(self):
        self._reports: Dict[str, Report] = {}

    def add(self, report: Report) -> None:
        self._reports[report.id] = report

    def get(self, report_id: str) -> Report:
        return self._reports[report_id]

    def summary(self, report_id: str) -> Dict:
        report = self.get(report_id)
        values = [r["value"] for r in report.records]
        total = sum(values)
        # NOTE: latent bug — divides by zero when there are no records.
        average = total / len(values)
        return {"id": report.id, "count": len(values), "total": total, "average": average}
