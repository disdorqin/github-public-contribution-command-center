"""CLI entry point.

Usage:
  python -m contrib_center.main run --mode safe
  python -m contrib_center.main scan-own
  python -m contrib_center.main scout
  python -m contrib_center.main update-profile
  python -m contrib_center.main report
  python -m contrib_center.main check-visibility OWNER/REPO
  python -m contrib_center.main llm-check
"""

from __future__ import annotations

import argparse
import json
import sys

from . import github_client, orchestrator, visibility_guard
from .policy import Policy


def _cmd_check_visibility(repo: str) -> int:
    policy = Policy.load()
    print(f"[contrib-center] checking {repo} (mode={policy.mode})")
    print(f"[contrib-center] whitelist size: {len(policy.public_repos)}")
    try:
        vis = visibility_guard.guard_repo_operation(repo, "check", policy)
        print(json.dumps(vis.to_dict(), indent=2))
        return 0
    except visibility_guard.PermissionError_ as e:
        print(str(e))
        return 2


def _cmd_run(mode: str) -> int:
    out = orchestrator.run(mode=mode)
    print(json.dumps(out["profile_status"], indent=2))
    return 0


def _cmd_scan_own() -> int:
    rows = orchestrator.scan_own()
    print(json.dumps(rows, indent=2, default=str))
    return 0


def _cmd_scout() -> int:
    rows = orchestrator.scout()
    print(json.dumps(rows, indent=2, default=str))
    return 0


def _cmd_update_profile() -> int:
    print(json.dumps(orchestrator.update_profile(), indent=2))
    return 0


def _cmd_report() -> int:
    print(orchestrator.regenerate_report())
    return 0


def _cmd_llm_check() -> int:
    try:
        from .llm_router import llm_check
        results = llm_check()
        print(json.dumps(results, indent=2))
        all_ok = all(r.get("ok", False) for r in results.values())
        return 0 if all_ok else 1
    except ImportError:
        print(json.dumps({"error": "llm_router module not available"}, indent=2))
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="contrib_center.main")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument(
        "--mode", default="safe", choices=["safe", "assisted", "autopilot"]
    )

    sub.add_parser("scan-own")
    sub.add_parser("scout")
    sub.add_parser("update-profile")
    sub.add_parser("report")
    sub.add_parser("llm-check")

    p_check = sub.add_parser("check-visibility")
    p_check.add_argument("repo")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args.mode)
    if args.cmd == "scan-own":
        return _cmd_scan_own()
    if args.cmd == "scout":
        return _cmd_scout()
    if args.cmd == "update-profile":
        return _cmd_update_profile()
    if args.cmd == "report":
        return _cmd_report()
    if args.cmd == "llm-check":
        return _cmd_llm_check()
    if args.cmd == "check-visibility":
        return _cmd_check_visibility(args.repo)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
