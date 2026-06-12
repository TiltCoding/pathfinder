import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reports.service import Report, ReportService  # noqa: E402
from api.routes import build_router  # noqa: E402


def make_service():
    svc = ReportService()
    svc.add(Report(id="r1", records=[{"value": 10}, {"value": 20}, {"value": 30}]))
    return svc


def test_summary_basic():
    svc = make_service()
    s = svc.summary("r1")
    assert s["count"] == 3
    assert s["total"] == 60
    assert s["average"] == 20


def test_router_dispatch():
    svc = make_service()
    router = build_router(svc)
    assert router.dispatch("GET", "/reports/r1")["total"] == 60
