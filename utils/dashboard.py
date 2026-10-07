"""
output/dashboard.html - the run at a glance: totals, the campaigns in
output/campaigns.json, and every test with its outcome and failure detail.
Plain HTML, no scripts, so it opens anywhere (and in CI artifacts).
"""
import html
import os

from utils.config import OUTPUT_DIR
from utils.store import all_campaign_results

STAGES = (
    ("test_00", "Preflight"),
    ("test_10", "Campaign"),
    ("test_20", "Registers"),
    ("test_30", "User mapping"),
    ("test_40", "Payment"),
)
OUTCOME_TEXT = {"passed": "Passed", "failed": "Failed", "skipped": "Skipped", "error": "Error"}


def _stage(nodeid):
    return next((label for prefix, label in STAGES if prefix in nodeid), "Other")


def generate_dashboard(results, path=None):
    path = path or os.path.join(OUTPUT_DIR, "dashboard.html")
    e = html.escape

    campaign_rows = "".join(
        f"<tr><td>{e(ctype)}</td><td>{e(r.get('campaignNumber', ''))}</td>"
        f"<td>{e(r.get('campaignName') or '')}</td>"
        f"<td>{len(r.get('users') or [])}</td></tr>"
        for ctype, r in sorted(all_campaign_results().items()))

    test_rows = "".join(
        f"<tr class='{e(t['outcome'])}'><td>{e(_stage(t['name']))}</td>"
        f"<td><code>{e(t['name'])}</code></td>"
        f"<td>{OUTCOME_TEXT.get(t['outcome'], e(t['outcome']))}</td>"
        f"<td>{t['duration']}s</td>"
        f"<td>{'<details><summary>detail</summary><pre>' + e(t['detail']) + '</pre></details>' if t.get('detail') else ''}</td></tr>"
        for t in results["tests"])

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>HCM attendance + payment - test run</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem; color: #1f2328; }}
 table {{ border-collapse: collapse; margin: .5rem 0 2rem; }}
 th, td {{ border: 1px solid #d0d7de; padding: .35rem .6rem; text-align: left; vertical-align: top; }}
 th {{ background: #f6f8fa; }}
 tr.failed td, tr.error td {{ background: #ffebe9; }}
 tr.skipped td {{ color: #57606a; }}
 pre {{ white-space: pre-wrap; max-width: 70rem; }}
</style></head><body>
<h1>HCM attendance + payment - test run</h1>
<p>{e(results['start_time'])} to {e(results['end_time'])}</p>
<table><caption>Totals</caption>
<tr><th scope="col">Total</th><th scope="col">Passed</th><th scope="col">Failed</th><th scope="col">Skipped</th></tr>
<tr><td>{results['total']}</td><td>{results['passed']}</td><td>{results['failed']}</td><td>{results['skipped']}</td></tr>
</table>
<h2>Campaigns</h2>
<table><tr><th scope="col">Type</th><th scope="col">Campaign number</th><th scope="col">Name</th><th scope="col">Users created</th></tr>
{campaign_rows or '<tr><td colspan="4">none recorded</td></tr>'}</table>
<h2>Tests</h2>
<table><tr><th scope="col">Stage</th><th scope="col">Test</th><th scope="col">Outcome</th><th scope="col">Duration</th><th scope="col">Detail</th></tr>
{test_rows}</table>
</body></html>
"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path
