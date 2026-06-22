"""Thin wrapper around the ``gh`` CLI.

Every method that touches a repo requires the repo's full name and
routes through the appropriate public-only guard.

* Own-repo operations  → ``guard_own_repo_operation`` (allowlist + public proof).
* External search results are NOT pre-filtered here; the caller
  (``issue_scout``) applies ``guard_external_public_repo_read``
  before generating any patch.
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
        encoding="utf-8",  # Force UTF-8 encoding
        timeout=timeout,
        check=False,
    )
    return (proc.returncode, proc.stdout, proc.stderr)


def auth_status() -> dict[str, Any]:
    rc, out, err = _gh(["auth", "status"])
    return {
        "available": shutil.which("gh") is not None,
        "logged_in": rc == 0,
        "stderr": err.strip() if err else "",
        "stdout": out.strip() if out else "",
    }


def search_issues(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """Search PUBLIC open issues (global feed).

    Each returned issue is later vetted by ``issue_scout`` through
    ``guard_external_public_repo_read`` before any patch work.

    Note: ``gh search issues --json`` does NOT support the ``body`` field.
    Callers should use ``get_issue_body()`` to fetch the full body when needed.
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


def get_issue_body(issue_url: str, timeout: int = 30) -> str:
    """Fetch the full body of a public issue using ``gh issue view``.

    Args:
        issue_url: Full URL of the issue (e.g., https://github.com/owner/repo/issues/123)
        timeout: Timeout in seconds for the gh command.

    Returns:
        The issue body as a string. Returns empty string on failure.
    """
    rc, out, err = _gh(
        [
            "issue",
            "view",
            issue_url,
            "--json",
            "body",
            "--jq",
            ".body",
        ],
        timeout=timeout,
    )
    if rc != 0:
        logger.log_action(
            "gh_issue_body_fetch_failed",
            issue_url=issue_url,
            rc=rc,
            stderr=err.strip()[:300],
        )
        return ""
    return out.strip()


def list_own_repo_issues(
    full_name: str, policy: Policy, limit: int = 20
) -> list[dict[str, Any]]:
    """List issues for YOUR own public repo (requires allowlist membership)."""
    visibility_guard.guard_own_repo_operation(full_name, "list_issues", policy)
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
    """Fetch README content for YOUR own public repo."""
    visibility_guard.guard_own_repo_operation(full_name, "read_readme", policy)
    rc, out, err = _gh(["api", f"repos/{full_name}/readme", "--jq", ".content"])
    if rc != 0:
        return ""
    return out


def repo_metadata(full_name: str, policy: Policy) -> dict[str, Any]:
    """Fetch repo metadata for YOUR own public repo."""
    visibility_guard.guard_own_repo_operation(full_name, "read_metadata", policy)
    rc, out, err = _gh(["api", f"repos/{full_name}"])
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}
