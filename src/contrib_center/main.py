"""CLI entry point.

Usage:
  python -m contrib_center.main run --mode safe
  python -m contrib_center.main scan-own
  python -m contrib_center.main scout
  python -m contrib_center.main update-profile
  python -m contrib_center.main report
  python -m contrib_center.main check-visibility OWNER/REPO
  python -m contrib_center.main llm-check
  python -m contrib_center.main assisted-pr --target-issue-url URL --confirm-publish true
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


def _cmd_assisted_pr(
    target_issue_url: str,
    mode: str,
    confirm_publish: bool,
    dry_run: bool = False,
    patch_workdir: str | None = None,
) -> int:
    """Handle assisted-pr command for external PR publishing."""
    try:
        import json
        import subprocess
        from pathlib import Path

        from .external_pr_publisher import publish_external_pr, dry_run_external_pr
        from .policy import Policy

        policy = Policy.load()

        # Override policy.mode with CLI argument
        policy.mode = mode

        if policy.mode != "assisted":
            print(json.dumps({
                "ok": False,
                "error": "assisted-pr requires --mode assisted",
                "current_mode": policy.mode,
                "hint": "Use: --mode assisted",
            }, indent=2))
            return 1

        # Validate patch_workdir
        if confirm_publish and not dry_run:
            if not patch_workdir:
                print(json.dumps({
                    "ok": False,
                    "error": "--patch-workdir is required when --confirm-publish=true",
                    "hint": "Use --dry-run to validate without publishing",
                }, indent=2))
                return 1

            patch_path = Path(patch_workdir)
            if not patch_path.exists():
                print(json.dumps({
                    "ok": False,
                    "error": f"patch_workdir does not exist: {patch_workdir}",
                    "skipped_reason": "patch_workdir_missing",
                }, indent=2))
                return 1

            # Check if there's an actual diff
            proc = subprocess.run(
                ["git", "diff"],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(patch_path),
            )
            rc = proc.returncode
            out = proc.stdout
            if rc != 0 or not out.strip():
                print(json.dumps({
                    "ok": False,
                    "error": "No diff found in patch_workdir",
                    "skipped_reason": "empty_patch",
                }, indent=2))
                return 1

        # Extract upstream repo from issue URL
        # URL format: https://github.com/OWNER/REPO/issues/NUMBER
        parts = target_issue_url.rstrip("/").split("/")
        if len(parts) < 5 or "github.com" not in target_issue_url:
            print(json.dumps({
                "ok": False,
                "error": "Invalid issue URL format",
                "expected": "https://github.com/OWNER/REPO/issues/NUMBER",
            }, indent=2))
            return 1

        upstream_repo = f"{parts[-4]}/{parts[-3]}"

        # Determine patch_workdir
        patch_path = Path(patch_workdir) if patch_workdir else None

        # Generate PR title and body
        proc = subprocess.run(
            ["gh", "issue", "view", target_issue_url, "--json", "title,body"],
            capture_output=True,
            text=True,
            check=False,
        )
        rc = proc.returncode
        out = proc.stdout
        pr_title = f"Fix issue #{parts[-1]}"
        pr_body = f"Address issue: {target_issue_url}"
        if rc == 0:
            try:
                import json
                issue_data = json.loads(out)
                pr_title = f"{issue_data.get('title', pr_title)} (#{parts[-1]})"
                pr_body = issue_data.get("body", pr_body)[:500]  # Truncate
            except Exception:
                pass

        # Execute
        if dry_run or not confirm_publish:
            result = dry_run_external_pr(
                issue_url=target_issue_url,
                upstream_repo=upstream_repo,
                patch_workdir=patch_path,
                pr_title=pr_title,
                policy=policy,
            )
        else:
            result = publish_external_pr(
                issue_url=target_issue_url,
                upstream_repo=upstream_repo,
                patch_workdir=patch_path,
                pr_title=pr_title,
                pr_body=pr_body,
                policy=policy,
                confirm_publish=confirm_publish,
            )

        # Output JSON result
        output = {
            "ok": result.ok,
            "mode": policy.mode,
            "upstream_repo": result.upstream_repo,
            "fork_repo": result.fork_repo,
            "branch": result.branch,
            "pr_url": result.pr_url,
            "error": result.error,
            "skipped_reason": result.skipped_reason,
            "patch_stats": result.patch_stats,
            "safety": {
                "public_only": True,
                "private_touched": 0,
                "external_comments": 0,
                "external_issues_created": 0,
                "stars_added": 0,
            },
        }
        print(json.dumps(output, indent=2, default=str))
        return 0 if result.ok else 1

    except Exception as e:
        print(json.dumps({
            "ok": False,
            "error": str(e),
        }, indent=2))
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

    p_assisted_pr = sub.add_parser("assisted-pr")
    p_assisted_pr.add_argument("--target-issue-url", required=True, help="Public issue URL")
    p_assisted_pr.add_argument(
        "--mode",
        default="safe",
        choices=["safe", "assisted"],
        help="Mode: safe (default) or assisted",
    )
    p_assisted_pr.add_argument(
        "--confirm-publish",
        required=True,
        choices=["true", "false"],
        help="Must be 'true' to publish PR",
    )
    p_assisted_pr.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode (no actual PR created)",
    )
    p_assisted_pr.add_argument(
        "--patch-workdir",
        default=None,
        help="Path to directory containing git diff (required for real publish)",
    )

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
    if args.cmd == "assisted-pr":
        confirm = args.confirm_publish == "true"
        return _cmd_assisted_pr(
            args.target_issue_url,
            args.mode,
            confirm,
            args.dry_run,
            args.patch_workdir,
        )
    if args.cmd == "check-visibility":
        return _cmd_check_visibility(args.repo)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
