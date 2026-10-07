"""
Session plumbing, as in api_automation_project:

  * output/failed_requests/<test>.json - the last request/response of every
    failing test that used the ``hcm`` client
  * output/test_results.json            - per-test outcome and duration
  * output/dashboard.html               - the run at a glance, with the
                                          campaigns it created
"""
import json
import os
import re
from datetime import datetime

import pytest

from utils.config import OUTPUT_DIR
from utils.dashboard import generate_dashboard

FAILED_REQUESTS_DIR = os.path.join(OUTPUT_DIR, "failed_requests")


def pytest_sessionstart(session):
    os.makedirs(FAILED_REQUESTS_DIR, exist_ok=True)
    for name in os.listdir(FAILED_REQUESTS_DIR):
        os.remove(os.path.join(FAILED_REQUESTS_DIR, name))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    client = item.funcargs.get("hcm")
    exchange = getattr(client, "last_exchange", None)
    if not exchange:
        return
    safe = re.sub(r"[^\w]", "_", item.nodeid.replace("tests/", "").replace(".py::", "__"))
    with open(os.path.join(FAILED_REQUESTS_DIR, f"{safe}.json"), "w", encoding="utf-8") as fh:
        json.dump({"test": item.nodeid, "timestamp": datetime.now().isoformat(),
                   "error": str(report.longrepr)[-4000:], **exchange}, fh, indent=2)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    stats = terminalreporter.stats
    reports = [r for key in ("passed", "failed", "skipped", "error")
               for r in stats.get(key, [])
               if r.when == "call" or (r.when == "setup" and r.outcome != "passed")]
    tests = []
    for r in reports:
        info = {"name": r.nodeid, "outcome": r.outcome, "duration": round(r.duration, 2)}
        if r.outcome != "passed":
            info["detail"] = (r.longrepr[2] if isinstance(r.longrepr, tuple)
                              else str(r.longrepr))[-4000:]
        tests.append(info)

    start = datetime.fromtimestamp(getattr(terminalreporter, "_sessionstarttime", 0)
                                   or datetime.now().timestamp())
    results = {
        "start_time": start.isoformat(), "end_time": datetime.now().isoformat(),
        "total": len(tests),
        "passed": sum(t["outcome"] == "passed" for t in tests),
        "failed": sum(t["outcome"] == "failed" for t in tests),
        "skipped": sum(t["outcome"] == "skipped" for t in tests),
        "tests": tests,
    }
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "test_results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    try:
        path = generate_dashboard(results)
        terminalreporter.write_line(f"\nDashboard: file://{path}")
    except Exception as e:  # the report must never fail the run
        terminalreporter.write_line(f"\nWarning: dashboard not generated: {e}")
