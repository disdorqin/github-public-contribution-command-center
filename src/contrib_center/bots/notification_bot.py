"""Notification stub.

In Safe Mode v1 we only print a one-line summary to stdout. The full
report is at reports/YYYY-MM-DD.md.
"""

from __future__ import annotations


def notify(report: dict, report_path: str) -> None:
    counters = report.get("counters", {})
    print(
        "[contrib-center] daily run complete: "
        f"scanned={counters.get('public_repos_scanned', 0)} "
        f"skipped={counters.get('repos_skipped', 0)} "
        f"candidates={len(report.get('candidates', []))} "
        f"patches={counters.get('patches_generated', 0)} "
        f"report={report_path}"
    )
