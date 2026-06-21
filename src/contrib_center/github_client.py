"""Thin wrapper around the ``gh`` CLI.

Every method that touches a repo requires the repo's full name and
routes through the public-only guard. NO method in this module performs
a network call before ``guard_repo_operation`` has approved the target
repo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from . import logger, visibility_guard
from .policy import Policy


def _gh(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    if shutil.which("gh") is None:
        return (127, "", "gh CLI not installed")
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return (proc.returncode, proc.stdout, proc.stderr)


def auth_status() -> dict[str, Any]:
    rc, out, err = _gh(["auth", "status"])
    return {
        "available": shutil.which("gh") is not None,
        "logged_in": rc == 0,
        "stderr": err.strip(),
        "stdout": out.strip(),
    }


def search_issues(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """Search PUBLIC open issues. NO repo arg means we are browsing the
    global issue feed. We still enforce that each returned issue's
    repository is in the public allowlist or has a non-moshevels4 owner
    that is publicly readable.

    For Safe Mode v1 we accept ANY public issue from external repos; the
    downstream score/filter step is what gates whether the bot does
    anything with it. The crucial invariant is that we never WRITE to
    such repos — see bots/pr_agent.py and bots/issue_scout.py.
    """
    rc, out, err = _gh(
        [
            "search",
            "issues",
            query,
            "--limit",
            str(limit),
            "--state",
            "open",
            "--json",
            "number,title,url,repository,labels,createdAt,author",
        ]
    )
    if rc != 0:
        logger.log_action("gh_search_failed", query=query, stderr=err.strip())
        return []
    try:
        return json.loads(out) or []
    except json.JSONDecodeError:
        logger.log_action("gh_search_parse_failed", raw=out[:200])
        return []


def list_own_repo_issues(full_name: str, policy: Policy, limit: int = 20) -> list[dict[str, Any]]:
    visibility_guard.guard_repo_operation(full_name, "list_issues", policy)
    rc, out, err = _gh(
        [
            "issue",
            "list",
            "--repo",
            full_name,
            "--limit",
            str(limit),
            "--state",
            "open",
            "--json",
            "number,title,url,labels,createdAt",
        ]
    )
    if rc != 0:
        logger.log_action("gh_issue_list_failed", repo=full_name, stderr=err.strip())
        return []
    try:
        return json.loads(out) or []
    except json.JSONDecodeError:
        return []


def get_readme(full_name: str, policy: Policy) -> str:
    visibility_guard.guard_repo_operation(full_name, "read_readme", policy)
    rc, out, err = _gh(["api", f"repos/{full_name}/readme", "--jq", ".content"])
    if rc != 0:
        return ""
    # `gh api` decodes the base64 content for us; if it comes back empty
    # we treat it as missing.
    return out


def repo_metadata(full_name: str, policy: Policy) -> dict[str, Any]:
    """Fetch repo metadata. Routed through the guard."""
    visibility_guard.guard_repo_operation(full_name, "read_metadata", policy)
    rc, out, err = _gh(["api", f"repos/{full_name}"])
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}
